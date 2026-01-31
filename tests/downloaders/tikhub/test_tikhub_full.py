"""
完整的 TikHub 下载器集成测试。

模拟实际的下载场景，测试完整流程。
"""

import asyncio
import os
import sys
from pathlib import Path

# 设置标准输出编码为 UTF-8（Windows）
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")

# 设置环境变量，加载 .env.development
os.environ["ENV_FILE"] = ".env.development"

# 导入项目模块
from src.config import Settings
from src.downloaders.tikhub_downloader import TikHubDownloader


async def test_full_download():
    """测试完整的下载流程（仅字幕）。"""
    print("=" * 60)
    print("TikHub 下载器集成测试（仅字幕）")
    print("=" * 60)

    # 测试配置
    video_url = "https://www.youtube.com/watch?v=ADW8IDQ-5Ws"
    video_id = "ADW8IDQ-5Ws"
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n测试参数:")
    print(f"  视频 URL: {video_url}")
    print(f"  视频 ID: {video_id}")
    print(f"  输出目录: {output_dir}")

    # 加载配置
    print(f"\n加载配置...")
    settings = Settings()
    print(f"  TikHub API Key: {settings.tikhub_api_key[:20] if settings.tikhub_api_key else 'None'}...")
    print(f"  HTTP Proxy: {settings.http_proxy or '(未设置)'}")
    print(f"  HTTPS Proxy: {settings.https_proxy or '(未设置)'}")

    # 初始化下载器
    print(f"\n初始化 TikHub 下载器...")
    downloader = TikHubDownloader(settings)
    print(f"  下载器名称: {downloader.name}")
    print(f"  是否可用: {downloader.is_available}")

    if not downloader.is_available:
        print("[FAIL] TikHub 下载器不可用（API key 未配置）")
        return

    # 开始下载
    print(f"\n开始下载测试...")
    print(f"  include_audio: False")
    print(f"  include_transcript: True")

    try:
        result = await downloader.download(
            video_url=video_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=False,
            include_transcript=True,
        )

        print(f"\n[OK] 下载成功!")
        print(f"\n下载结果:")
        print(f"  成功: {result.success}")
        print(f"  下载器: {result.downloader}")
        print(f"  有字幕: {result.has_transcript}")
        print(f"  音频路径: {result.audio_path}")
        print(f"  字幕路径: {result.transcript_path}")

        if result.video_metadata:
            print(f"\n视频元数据:")
            print(f"  标题: {result.video_metadata.title}")
            print(f"  作者: {result.video_metadata.author}")
            print(f"  时长: {result.video_metadata.duration}秒")

        # 检查文件
        if result.transcript_path:
            file_size = result.transcript_path.stat().st_size
            print(f"\n字幕文件:")
            print(f"  路径: {result.transcript_path}")
            print(f"  大小: {file_size} 字节")

            # 显示前几行
            content = result.transcript_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            print(f"  前 10 行:")
            for line in lines[:10]:
                print(f"    {line}")

    except Exception as e:
        print(f"\n[FAIL] 下载失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return

    finally:
        # 关闭下载器
        await downloader.close()

    print("\n" + "=" * 60)
    print("[OK] 测试完成")
    print("=" * 60)


async def main():
    """主测试流程。"""
    await test_full_download()


if __name__ == "__main__":
    asyncio.run(main())
