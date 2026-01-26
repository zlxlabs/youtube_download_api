# 手动测试脚本

本目录包含用于诊断和测试 TikHub 下载器的手动测试脚本。

## 测试脚本列表

### 1. test_tikhub_connection.py
**功能**：测试 TikHub API 基本连接

- 检查网络连接
- 验证 API 鉴权
- 测试代理配置

**运行**：
```bash
cd /path/to/project
uv run python tests/manual/test_tikhub_connection.py
```

### 2. test_tikhub_subtitle.py
**功能**：测试字幕下载完整流程

- 获取字幕 URL
- 调用 TikHub 字幕 API
- 生成 SRT 文件

**运行**：
```bash
uv run python tests/manual/test_tikhub_subtitle.py
```

### 3. test_proxy_env.py
**功能**：诊断代理环境变量配置

- 检查系统代理设置
- 测试 httpx 客户端的不同代理模式
- 排查连接问题

**运行**：
```bash
uv run python tests/manual/test_proxy_env.py
```

### 4. test_tikhub_full.py
**功能**：完整的 TikHub 下载器集成测试

- 加载实际配置
- 模拟真实下载场景
- 验证端到端流程

**运行**：
```bash
uv run python tests/manual/test_tikhub_full.py
```

### 5. test_retry_mechanism.py
**功能**：测试下载器重试机制

- 验证指数退避策略（1s, 2s, 4s）
- 测试最大重试次数（3次）
- 测试可重试/不可重试错误的处理
- 演示 4 种场景：
  - 场景 1：第一次成功（无重试）
  - 场景 2：第 2 次重试成功
  - 场景 3：达到最大重试次数
  - 场景 4：不可重试的错误

**运行**：
```bash
uv run python tests/manual/test_retry_mechanism.py
```

**预期输出**：
- 场景 1：1 次调用，0 秒
- 场景 2：3 次调用，~3 秒
- 场景 3：4 次调用，~7 秒
- 场景 4：1 次调用，0 秒

## 测试输出

所有测试脚本的输出文件都会保存在 `tests/manual/output/` 目录下，该目录已被 `.gitignore` 忽略。

## 注意事项

1. **API Key**：这些脚本使用 `.env.development` 中的 TikHub API key
2. **网络要求**：需要能够访问 TikHub API（可能需要代理）
3. **Python 版本**：需要 Python 3.11+
4. **依赖管理**：使用 `uv` 管理依赖

## 故障排查

如果测试失败，请检查：

1. `.env.development` 文件是否存在且配置正确
2. TikHub API key 是否有效
3. 网络连接是否正常
4. 是否需要配置代理

## 相关文档

诊断报告和分析文档位于：
- `docs/diagnosis/TIKHUB_DIAGNOSIS_REPORT.md` - 完整诊断报告
- `docs/diagnosis/RETRY_MECHANISM_ANALYSIS.md` - 重试机制分析（旧版）
- `docs/diagnosis/DOWNLOADER_RETRY_IMPROVEMENT.md` - 下载器级重试改进（新版）
- `docs/diagnosis/CHANGELOG.md` - ConnectError 修复记录
