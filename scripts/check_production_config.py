#!/usr/bin/env python3
"""
生产环境配置检查工具

针对用户实际配置进行深度检查，诊断 RATE_LIMITED 问题
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

# 添加 src 目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import httpx
except ImportError:
    print("❌ 缺少 httpx 模块")
    print("   请安装: pip install httpx")
    sys.exit(1)


class ProductionConfigChecker:
    """生产环境配置检查器"""

    def __init__(self, env_file: str = "docker/.env"):
        """初始化检查器"""
        self.env_file = Path(env_file)
        self.config = {}
        self.issues = []
        self.warnings = []
        self.load_config()

    def load_config(self):
        """加载 .env 配置文件"""
        if not self.env_file.exists():
            print(f"❌ 配置文件不存在: {self.env_file}")
            sys.exit(1)

        print(f"📄 加载配置文件: {self.env_file}")
        with open(self.env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    self.config[key.strip()] = value.strip()

        print(f"✓ 已加载 {len(self.config)} 个配置项\n")

    def print_header(self, title: str):
        """打印标题"""
        print(f"\n{'=' * 60}")
        print(f"  {title}")
        print(f"{'=' * 60}\n")

    def check_pot_server(self) -> bool:
        """深度检查 PO Token 服务"""
        self.print_header("检查 PO Token 服务")

        pot_url = self.config.get("POT_SERVER_URL", "")
        print(f"配置的地址: {pot_url}")

        if not pot_url:
            self.issues.append("POT_SERVER_URL 未配置")
            print("❌ POT_SERVER_URL 未配置")
            return False

        # 检查 URL 类型
        if "192.168" in pot_url or "10." in pot_url or "172." in pot_url:
            print(f"ℹ️  检测到内网地址: {pot_url}")
            print("   请确保:")
            print("   1. PO Token 服务运行在此 IP 的 4416 端口")
            print("   2. YouTube API 服务能够访问此 IP")
            print("   3. 防火墙允许 4416 端口通信")

        # 尝试连接
        print(f"\n🔍 测试连接 {pot_url}/health ...")
        try:
            response = httpx.get(f"{pot_url}/health", timeout=10.0)
            if response.status_code == 200:
                print(f"✅ PO Token 服务连接成功 (HTTP {response.status_code})")
                print(f"   响应内容: {response.text[:100]}")
                return True
            else:
                self.warnings.append(
                    f"PO Token 服务返回异常状态码: {response.status_code}"
                )
                print(f"⚠️  返回状态码: {response.status_code}")
                print(f"   响应内容: {response.text[:200]}")
                return False
        except httpx.ConnectTimeout:
            self.issues.append("PO Token 服务连接超时")
            print("❌ 连接超时（10秒）")
            print("\n可能原因:")
            print("   1. PO Token 服务未启动")
            print("   2. IP 地址配置错误")
            print("   3. 网络不通（防火墙/路由问题）")
            print("\n排查步骤:")
            print(f"   # 从 YouTube API 容器内测试")
            print(f"   docker exec youtube-api curl -v {pot_url}/health")
            return False
        except httpx.ConnectError as e:
            self.issues.append(f"无法连接到 PO Token 服务: {e}")
            print(f"❌ 连接失败: {type(e).__name__}")
            print(f"   错误详情: {e}")
            return False
        except Exception as e:
            self.issues.append(f"测试 PO Token 服务时出错: {e}")
            print(f"❌ 未知错误: {e}")
            return False

    def check_cookie_file(self) -> bool:
        """深度检查 Cookie 文件"""
        self.print_header("检查 Cookie 文件")

        cookie_file = self.config.get("COOKIE_FILE", "")
        if not cookie_file:
            self.warnings.append("未配置 Cookie 文件")
            print("⚠️  未配置 Cookie 文件")
            print("   强烈建议配置以提高成功率")
            return False

        print(f"配置的路径: {cookie_file}")

        # 检查路径类型
        if cookie_file.startswith("./"):
            print("⚠️  使用相对路径: " + cookie_file)
            print("   在 Docker 容器中，相对路径基于工作目录")
            print("   建议检查实际文件位置")

        # 尝试多个可能的路径
        possible_paths = [
            Path(cookie_file),
            Path("docker") / cookie_file.lstrip("./"),
            Path("docker/data") / Path(cookie_file).name,
            Path(cookie_file).absolute(),
        ]

        found = False
        for path in possible_paths:
            if path.exists():
                found = True
                print(f"\n✓ 找到 Cookie 文件: {path}")
                print(f"  大小: {path.stat().st_size} 字节")

                # 检查文件内容
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                        if "Netscape HTTP Cookie File" in first_line:
                            print("  ✓ 文件格式正确（Netscape 格式）")
                        else:
                            self.warnings.append("Cookie 文件格式可能不正确")
                            print(f"  ⚠️  格式可能不正确")
                            print(f"     首行: {first_line[:50]}")

                        # 统计 Cookie 数量
                        f.seek(0)
                        cookie_count = sum(
                            1 for line in f if line.strip() and not line.startswith("#")
                        )
                        print(f"  Cookie 数量: {cookie_count}")

                        if cookie_count == 0:
                            self.warnings.append("Cookie 文件为空")
                            print("  ⚠️  Cookie 文件为空")

                        # 检查修改时间
                        mtime = datetime.fromtimestamp(path.stat().st_mtime)
                        age_days = (datetime.now() - mtime).days
                        print(f"  最后修改: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"  文件年龄: {age_days} 天")

                        if age_days > 30:
                            self.warnings.append(
                                f"Cookie 文件已有 {age_days} 天，可能已过期"
                            )
                            print(f"  ⚠️  文件已有 {age_days} 天，建议重新导出")

                except Exception as e:
                    self.warnings.append(f"读取 Cookie 文件失败: {e}")
                    print(f"  ⚠️  读取失败: {e}")

                break

        if not found:
            self.issues.append(f"Cookie 文件不存在: {cookie_file}")
            print(f"\n❌ 在以下位置都未找到 Cookie 文件:")
            for path in possible_paths:
                print(f"   - {path}")
            print("\n建议:")
            print("   1. 重新导出 YouTube Cookie")
            print("   2. 确认文件路径配置正确")
            print("   3. 检查 Docker 容器内的文件挂载")
            return False

        # 检查 Docker 挂载配置
        print("\n🐳 检查 Docker Compose 配置")
        compose_file = Path("docker/docker-compose.prod.yml")
        if compose_file.exists():
            with open(compose_file, "r", encoding="utf-8") as f:
                content = f.read()
                if "cookies.txt" in content:
                    print("  ✓ docker-compose.prod.yml 中已配置 Cookie 挂载")
                else:
                    self.warnings.append(
                        "docker-compose.prod.yml 中未配置 Cookie 挂载"
                    )
                    print("  ⚠️  docker-compose.prod.yml 中未找到 Cookie 挂载配置")
                    print("     建议添加:")
                    print("     volumes:")
                    print("       - ./cookies.txt:/app/cookies.txt:ro")

        return found

    def check_task_interval(self) -> bool:
        """检查任务间隔配置"""
        self.print_header("检查任务间隔配置")

        min_interval = int(self.config.get("TASK_INTERVAL_MIN", "0"))
        max_interval = int(self.config.get("TASK_INTERVAL_MAX", "0"))

        print(f"当前配置:")
        print(f"  最小间隔: {min_interval} 秒 ({min_interval / 60:.1f} 分钟)")
        print(f"  最大间隔: {max_interval} 秒 ({max_interval / 60:.1f} 分钟)")

        # 评估配置
        if min_interval < 300:
            self.warnings.append(f"最小间隔太短: {min_interval} 秒")
            print(f"\n⚠️  最小间隔 {min_interval}s 可能太短")
            print("   建议: 至少 300 秒（5 分钟）")
            return False

        if max_interval < 900:
            self.warnings.append(f"最大间隔太短: {max_interval} 秒")
            print(f"⚠️  最大间隔 {max_interval}s 可能太短")
            print("   建议: 至少 1800 秒（30 分钟）")

        # 针对"有时失败"的情况，给出更保守的建议
        if min_interval < 600:
            print(f"\n💡 针对当前'有时成功有时失败'的情况:")
            print(f"   建议进一步增加间隔:")
            print(f"   TASK_INTERVAL_MIN=600   # 10 分钟")
            print(f"   TASK_INTERVAL_MAX=1800  # 30 分钟")

        return True

    def check_proxy(self) -> bool:
        """检查代理配置"""
        self.print_header("检查代理配置")

        http_proxy = self.config.get("HTTP_PROXY", "")
        https_proxy = self.config.get("HTTPS_PROXY", "")

        if http_proxy or https_proxy:
            print("✓ 已配置代理")
            if http_proxy:
                print(f"  HTTP_PROXY: {http_proxy}")
            if https_proxy:
                print(f"  HTTPS_PROXY: {https_proxy}")
        else:
            print("ℹ️  未配置代理")
            print("\n如果服务器 IP 被 YouTube 限制，建议配置代理:")
            print("  HTTP_PROXY=http://proxy-server:port")
            print("  HTTPS_PROXY=http://proxy-server:port")

        return True

    def check_tikhub_api(self) -> bool:
        """检查 TikHub API 配置"""
        self.print_header("检查 TikHub API 配置")

        api_key = self.config.get("TIKHUB_API_KEY", "")
        if api_key:
            print(f"✓ 已配置 TikHub API Key")
            print(f"  Key: {api_key[:20]}...")

            # 提示可能的配额限制
            print("\n💡 TikHub API 注意事项:")
            print("   1. API 可能有调用配额限制")
            print("   2. 超过配额会导致字幕获取失败")
            print("   3. 建议查看 TikHub 控制台确认配额使用情况")
        else:
            print("ℹ️  未配置 TikHub API")
            print("   字幕获取将受到更多限制")

        return True

    def analyze_rate_limit_pattern(self) -> None:
        """分析 RATE_LIMITED 模式"""
        self.print_header("RATE_LIMITED 问题分析")

        print("📊 根据配置和'有时成功有时失败'的症状分析:\n")

        # 可能原因排序
        possible_causes = []

        # 1. Cookie 过期
        cookie_file = self.config.get("COOKIE_FILE", "")
        if cookie_file:
            for possible_path in [
                Path(cookie_file),
                Path("docker") / cookie_file.lstrip("./"),
            ]:
                if possible_path.exists():
                    mtime = datetime.fromtimestamp(possible_path.stat().st_mtime)
                    age_days = (datetime.now() - mtime).days
                    if age_days > 30:
                        possible_causes.append(
                            (
                                "🔴 高",
                                f"Cookie 文件已有 {age_days} 天，可能已过期",
                                "重新导出 YouTube Cookie",
                            )
                        )
                    break

        # 2. 间隔时间
        min_interval = int(self.config.get("TASK_INTERVAL_MIN", "0"))
        max_interval = int(self.config.get("TASK_INTERVAL_MAX", "0"))
        if min_interval < 600 or max_interval < 1800:
            possible_causes.append(
                (
                    "🟡 中",
                    f"任务间隔可能不够保守 (当前: {min_interval}-{max_interval}s)",
                    "增加到 600-1800 秒",
                )
            )

        # 3. PO Token 不稳定
        pot_url = self.config.get("POT_SERVER_URL", "")
        if "192.168" in pot_url:
            possible_causes.append(
                (
                    "🟡 中",
                    "PO Token 服务使用内网 IP，可能存在间歇性连接问题",
                    "检查网络稳定性，查看 pot-provider 日志",
                )
            )

        # 4. IP 被部分限制
        if not self.config.get("HTTP_PROXY"):
            possible_causes.append(
                (
                    "🟢 低",
                    "服务器 IP 可能被 YouTube 部分限制",
                    "尝试配置代理",
                )
            )

        # 5. TikHub 配额
        if self.config.get("TIKHUB_API_KEY"):
            possible_causes.append(
                (
                    "🟢 低",
                    "TikHub API 配额可能不足（影响字幕获取）",
                    "检查 TikHub 配额使用情况",
                )
            )

        # 打印分析结果
        if possible_causes:
            print("可能原因（按概率排序）:\n")
            for priority, cause, solution in possible_causes:
                print(f"{priority} {cause}")
                print(f"   → 建议: {solution}\n")
        else:
            print("✓ 配置看起来合理，需要进一步排查\n")

    def generate_recommendations(self) -> None:
        """生成修复建议"""
        self.print_header("修复建议")

        print("📝 根据检查结果，建议按以下顺序进行修复:\n")

        steps = []

        # Step 1: 检查并更新 Cookie
        if any("Cookie" in str(i) for i in self.issues + self.warnings):
            steps.append(
                (
                    "1. 更新 YouTube Cookie（最优先）",
                    [
                        "使用插件导出最新的 YouTube Cookie",
                        "确保 Cookie 来自已登录的账号",
                        "上传到 docker/cookies.txt 或 docker/data/cookies.txt",
                        "确认 docker-compose.prod.yml 中已挂载 Cookie",
                        "重启服务: docker-compose restart youtube-api",
                    ],
                )
            )

        # Step 2: 增加任务间隔
        min_interval = int(self.config.get("TASK_INTERVAL_MIN", "0"))
        if min_interval < 600:
            steps.append(
                (
                    "2. 增加任务间隔时间",
                    [
                        "编辑 docker/.env 文件",
                        "修改: TASK_INTERVAL_MIN=600",
                        "修改: TASK_INTERVAL_MAX=1800",
                        "重启服务应用配置",
                    ],
                )
            )

        # Step 3: 检查 PO Token 服务
        if any("PO Token" in str(i) for i in self.issues):
            steps.append(
                (
                    "3. 修复 PO Token 服务连接",
                    [
                        "检查 pot-provider 容器状态: docker ps | grep pot",
                        "查看 pot-provider 日志: docker logs pot-provider",
                        "从容器内测试连接: docker exec youtube-api curl -v <POT_URL>/health",
                        "如果连接失败，检查网络配置和防火墙",
                    ],
                )
            )

        # Step 4: 配置代理（可选）
        if not self.config.get("HTTP_PROXY"):
            steps.append(
                (
                    "4. 配置代理（可选，如果IP被限）",
                    [
                        "获取可用的代理服务器地址",
                        "在 .env 中配置: HTTP_PROXY=http://proxy:port",
                        "在 .env 中配置: HTTPS_PROXY=http://proxy:port",
                        "重启服务",
                    ],
                )
            )

        # Step 5: 监控和验证
        steps.append(
            (
                f"{len(steps) + 1}. 监控和验证",
                [
                    "重启服务后，查看启动日志确认配置",
                    "docker logs youtube-api | grep 'Anti-Bot'",
                    "创建测试任务观察成功率",
                    "持续监控 1-2 天，记录成功率变化",
                ],
            )
        )

        for title, actions in steps:
            print(f"\n{title}")
            for action in actions:
                print(f"   • {action}")

    def print_summary(self) -> None:
        """打印检查摘要"""
        self.print_header("检查摘要")

        print(f"配置文件: {self.env_file}")
        print(f"检查项目: 5 项")
        print(f"发现问题: {len(self.issues)} 个")
        print(f"发现警告: {len(self.warnings)} 个\n")

        if self.issues:
            print("❌ 严重问题:")
            for i, issue in enumerate(self.issues, 1):
                print(f"   {i}. {issue}")

        if self.warnings:
            print(f"\n⚠️  警告:")
            for i, warning in enumerate(self.warnings, 1):
                print(f"   {i}. {warning}")

        if not self.issues and not self.warnings:
            print("✅ 所有检查通过！")
        else:
            print(f"\n💡 请参考上面的'修复建议'部分进行修复")

    def run(self) -> int:
        """运行所有检查"""
        print("\n" + "=" * 60)
        print("  生产环境配置深度检查工具")
        print("=" * 60)

        # 运行检查
        self.check_pot_server()
        self.check_cookie_file()
        self.check_task_interval()
        self.check_proxy()
        self.check_tikhub_api()

        # 分析问题模式
        self.analyze_rate_limit_pattern()

        # 生成建议
        self.generate_recommendations()

        # 打印摘要
        self.print_summary()

        print("\n" + "=" * 60)
        print("  检查完成")
        print("=" * 60)

        return 0 if not self.issues else 1


def main():
    """主函数"""
    # 检查是否指定了配置文件
    env_file = "docker/.env" if len(sys.argv) < 2 else sys.argv[1]

    checker = ProductionConfigChecker(env_file)
    exit_code = checker.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
