# ConnectError 修复与文件整理记录

## 日期
2026-01-26

## 修复内容

### 1. TikHub 下载器 ConnectError 错误码修复

**问题**：`httpx.ConnectError` 被错误地归类为 `DOWNLOAD_FAILED`（不可重试），实际应该归类为 `NETWORK_ERROR`（可重试）。

**影响**：临时性网络问题（如 DNS 解析延迟、短暂的网络波动）会导致直接降级到 yt-dlp，而不是先尝试重试。

**修复**：在 `src/downloaders/tikhub_downloader.py` 中添加专门处理 `httpx.ConnectError` 的异常捕获。

**修复位置**：`src/downloaders/tikhub_downloader.py:213-224`

**修复代码**：
```python
except httpx.ConnectError as e:
    # 连接错误（网络不可达、DNS 失败等）
    # 这类错误通常是临时性的，应该重试而不是立即降级
    error_msg = str(e) if str(e) else "Network unreachable"
    logger.error(f"[tikhub] Connection failed: {error_msg}", exc_info=True)
    raise DownloaderError(
        message=f"Connection failed: {error_msg}",
        error_code=ErrorCode.NETWORK_ERROR,  # ← 关键修复
        downloader=self.name,
    ) from e
```

**效果**：
- ✅ 临时性网络问题会触发重试（通过 `should_retry()` 返回 True）
- ✅ 降级留给真正需要的情况（如 API 限流、认证失败）
- ✅ 节省 API 配额（不会因为临时网络问题就切换下载器）

### 2. 文件结构整理

**问题**：测试脚本和诊断报告散落在根目录，造成混乱。

**整理方案**：

#### 创建的目录结构
```
tests/
├── manual/              # 手动测试脚本目录
│   ├── README.md        # 测试脚本说明文档
│   ├── output/          # 测试输出目录（.gitignore）
│   ├── test_tikhub_connection.py
│   ├── test_tikhub_subtitle.py
│   ├── test_proxy_env.py
│   └── test_tikhub_full.py
│
docs/
└── diagnosis/           # 诊断报告目录
    ├── CHANGELOG.md                    # 本文档
    ├── TIKHUB_DIAGNOSIS_REPORT.md      # 完整诊断报告
    └── RETRY_MECHANISM_ANALYSIS.md     # 重试机制分析
```

#### 移动的文件

**从根目录移到 `tests/manual/`**：
- `test_tikhub_connection.py`
- `test_tikhub_subtitle.py`
- `test_proxy_env.py`
- `test_tikhub_full.py`

**从根目录移到 `docs/diagnosis/`**：
- `TIKHUB_DIAGNOSIS_REPORT.md`
- `RETRY_MECHANISM_ANALYSIS.md`

**删除的目录**：
- `test_output/`（旧的测试输出目录）
- `test_output_full/`（旧的测试输出目录）

#### 更新的文件

1. **测试脚本路径**
   - 更新输出目录为 `Path(__file__).parent / "output"`
   - 确保所有测试输出统一到 `tests/manual/output/`

2. **.gitignore**
   - 移除旧的忽略规则：
     - `test_output/`
     - `test_output_*/`
     - `test_tikhub_*.py`
     - `test_proxy_env.py`
     - `TIKHUB_DIAGNOSIS_REPORT.md`
   - 添加新的忽略规则：
     - `tests/manual/output/`

3. **诊断报告引用**
   - 更新文档中的文件路径引用
   - 更新运行脚本的示例命令

#### 新增的文件

1. **tests/manual/README.md**
   - 测试脚本说明文档
   - 包含每个脚本的功能说明和运行方法
   - 故障排查指南

2. **docs/diagnosis/CHANGELOG.md**（本文档）
   - 记录修复和整理的详细信息

## 测试验证

### 修复前行为
```
ConnectError 发生
  ↓
错误码: DOWNLOAD_FAILED
  ↓
should_retry() = False
  ↓
降级到 ytdlp
```

### 修复后行为
```
ConnectError 发生
  ↓
错误码: NETWORK_ERROR
  ↓
should_retry() = True
  ↓
重试 TikHub（最多1次）
  ↓
如果仍失败，降级到 ytdlp
```

## 影响范围

### 直接影响
- TikHub 下载器的错误处理逻辑
- 临时性网络问题的处理策略

### 间接影响
- 降低不必要的下载器切换
- 减少 API 配额浪费
- 提高系统容错能力

### 不影响
- 其他类型的错误处理（HTTP 错误、超时等）
- 降级机制的总体逻辑
- 熔断器功能

## 向后兼容性

✅ 完全向后兼容

- 只改变了 `ConnectError` 的错误码映射
- 不影响现有的降级机制
- 不改变 API 接口

## 后续建议

1. **监控 ConnectError 频率**
   - 如果频繁出现，可能需要检查网络环境
   - 考虑添加告警机制

2. **考虑添加指标**
   - 记录各类错误的发生频率
   - 记录重试成功率

3. **文档维护**
   - 保持测试脚本和文档的同步更新
   - 定期运行测试验证功能

## 相关文档

- 完整诊断报告：`docs/diagnosis/TIKHUB_DIAGNOSIS_REPORT.md`
- 重试机制分析：`docs/diagnosis/RETRY_MECHANISM_ANALYSIS.md`
- 测试脚本说明：`tests/manual/README.md`
