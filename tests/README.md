# Tests 目录说明

本文档说明测试目录的组织结构和运行方式。

## 目录结构

```
tests/
├── README.md                        # 本文档
├── conftest.py                      # 共享 pytest fixtures
├── TEST_RESULTS.md                  # 测试结果记录
│
├── unit/                            # 单元测试
│   ├── test_403_stop_fallback.py    # 下载器降级逻辑
│   ├── test_download_modes.py       # 下载模式测试
│   ├── test_ip_ban_passive_probe.py # IP 熔断机制
│   ├── test_metadata_completion.py  # 元数据补充
│   ├── test_pot_config.py           # POT 配置测试
│   ├── test_priority.py             # 优先级测试
│   └── test_transcript_detection_cache.py  # 字幕缓存
│
├── integration/                     # 集成测试
│   ├── test_priority_integration.py # 优先级集成
│   ├── test_scenario_priority.py    # 场景优先级
│   ├── test_simple.py              # 配置检查
│   └── test_youtube_data_api_integration.py  # YouTube Data API 集成
│
├── downloaders/                     # 下载器测试
│   ├── cdp/                        # CDP 下载器测试
│   │   ├── test_cdp_human_behavior.py
│   │   ├── test_cdp_human_behavior_integration.py
│   │   ├── test_cdp_isolation.py
│   │   ├── test_cdp_multipart.py
│   │   ├── test_cdp_pause_control.py
│   │   ├── test_cdp_reconnection.py
│   │   └── test_cdp_transcode.py
│   └── tikhub/                     # TikHub 下载器测试
│       ├── test_tikhub_connection.py
│       ├── test_tikhub_subtitle.py
│       └── test_tikhub_full.py
│
├── api/                             # API 测试
│   └── test_auth.py                # API 鉴权
│
├── services/                        # 服务层测试
│   └── test_manual_upload_service.py  # 人工上传服务
│
├── utils/                           # 工具类测试
│   └── test_helpers.py             # 辅助函数测试
│
└── manual/                          # 手动测试脚本（诊断用）
    ├── README.md                   # 手动测试说明
    ├── test_proxy_env.py           # 代理环境诊断
    ├── test_retry_mechanism.py     # 重试机制测试
    ├── test_cdp_manual.py          # CDP 手动测试
    └── test_cdp_human_behavior_manual.py  # CDP 人类行为手动测试
```

## 运行测试

### 运行所有测试

```bash
# 使用 uv 运行
uv run pytest

# 或直接运行 pytest（虚拟环境中）
pytest
```

### 运行特定类别的测试

```bash
# 只运行单元测试
uv run pytest tests/unit/

# 只运行集成测试
uv run pytest tests/integration/

# 只运行下载器测试
uv run pytest tests/downloaders/

# 只运行 API 测试
uv run pytest tests/api/

# 只运行服务层测试
uv run pytest tests/services/

# 只运行工具类测试
uv run pytest tests/utils/
```

### 运行特定下载器的测试

```bash
# 只运行 CDP 测试
uv run pytest tests/downloaders/cdp/

# 只运行 TikHub 测试
uv run pytest tests/downloaders/tikhub/
```

### 运行单个测试文件

```bash
uv run pytest tests/unit/test_priority.py
```

### 运行特定测试函数

```bash
uv run pytest tests/unit/test_priority.py::test_priority_enum_values -v
```

### 运行带标记的测试

```bash
# 运行异步测试
uv run pytest -m asyncio

# 跳过慢速测试
uv run pytest -m "not slow"

# 只运行集成测试（如果标记）
uv run pytest -m integration
```

### 查看测试覆盖率

```bash
uv run pytest --cov=src --cov-report=html --cov-report=term
```

覆盖率配置见 `pyproject.toml` 的 `[tool.coverage.*]`。

## 持续集成（CI）

GitHub Actions 在每次 push / PR 到 main 时自动运行（`.github/workflows/ci.yml`）：

1. `mypy src/` — 类型检查（必须零错误）
2. `pytest tests/` — 全部本地测试（自动排除 `requires_external` / `manual` 标记）

提交前请在本地先跑通这两步。

## 测试类型说明

### 单元测试 (unit/)

- **范围**：测试单个函数、类或模块
- **依赖**：使用 mock 对象，不依赖外部服务
- **速度**：快速运行（< 1秒）
- **示例**：优先级排序、错误码验证、配置解析

### 集成测试 (integration/)

- **范围**：测试多个模块的交互
- **依赖**：可能依赖真实的数据库、文件系统
- **速度**：中等速度（1-5 秒）
- **示例**：完整下载流程、任务队列处理

### 下载器测试 (downloaders/)

- **CDP**：Chrome DevTools Protocol 下载器测试
  - 人类行为模拟
  - 多部分下载
  - 暂停控制
  - 重连机制
  - 转码功能

- **TikHub**：TikHub API 下载器测试
  - 连接测试
  - 字幕下载
  - 完整集成

### 手动测试 (manual/)

- **用途**：诊断和调试工具
- **运行方式**：直接执行脚本（非 pytest）
- **示例**：
  ```bash
  uv run python tests/manual/test_cdp_manual.py
  ```
- **注意**：这些脚本通常需要特定的环境配置（如 CDP Chrome、代理等）

## 编写新测试

### 选择测试类型

1. **单元测试**：测试单个函数或小模块
2. **集成测试**：测试多个模块协作
3. **下载器测试**：新增下载器或下载器功能

### 命名规范

- 文件名：`test_<feature>_<aspect>.py`
- 测试类：`Test<FeatureName>`
- 测试函数：`test_<specific_behavior>`

示例：
```
test_priority.py
    class TestPriority:
        def test_priority_enum_values(self):
        def test_to_queue_priority(self):
        async def test_priority_queue_ordering(self):
```

### 使用 Fixtures

共享的 fixtures 定义在 `conftest.py` 中：
- `test_settings`：测试配置
- `test_db`：测试数据库
- `file_service`：文件服务
- `mock_downloader`：模拟下载器
- `mock_notifier`：模拟通知服务

示例：
```python
def test_something(test_settings: Settings):
    assert test_settings.api_key is not None
```

## 常见问题

### Q: 测试失败后如何调试？

```bash
# 显示详细输出
uv run pytest -vv

# 进入 pdb 调试器
uv run pytest --pdb

# 只运行失败的测试
uv run pytest --lf
```

### Q: 如何跳过某些测试？

```bash
# 跳过需要外部服务的测试
uv run pytest -m "not external"

# 跳过慢速测试
uv run pytest -m "not slow"
```

### Q: 手动测试如何运行？

查看 `tests/manual/README.md` 获取详细说明。基本步骤：

```bash
# 1. 确保环境配置正确（.env.development）
# 2. 启动必要的服务（如 CDP Chrome）
# 3. 直接运行脚本
uv run python tests/manual/test_cdp_manual.py
```

## 相关文档

- [测试结果记录](./TEST_RESULTS.md) - 历史测试结果
- [手动测试说明](./manual/README.md) - 手动测试详细指南
- [项目 README](../README.md) - 项目整体说明
