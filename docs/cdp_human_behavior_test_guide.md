# CDP 人类行为模拟测试指南

## 文档信息

- **版本**: v1.0
- **创建日期**: 2026-01-29
- **测试阶段**: 本地测试 → 灰度测试 → 全量上线

---

## 测试概览

本指南提供了 CDP 人类行为模拟功能的完整测试流程，包括单元测试、集成测试和手动测试。

### 测试文件清单

| 文件 | 类型 | 用途 |
|------|------|------|
| `tests/test_cdp_human_behavior.py` | 单元测试 | 测试各个方法的功能 |
| `tests/test_cdp_human_behavior_integration.py` | 集成测试 | 测试完整下载流程 |
| `tests/test_cdp_human_behavior_manual.py` | 手动测试 | 观察 Chrome 行为 |

---

## 第一阶段：环境准备

### 1. 安装依赖

```bash
# 确保在虚拟环境中
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/Mac
python -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 确保 Playwright 已安装
pip install playwright
```

### 2. 启动外部 Chrome

**重要**：Chrome 窗口必须保持可见，以便观察人类行为模拟。

#### Windows

```powershell
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir=C:\temp\chrome-cdp `
  --no-first-run `
  --no-default-browser-check
```

#### Mac

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check
```

#### Linux

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check
```

### 3. 验证 CDP 连接

```bash
# 验证 Chrome CDP 是否正常
curl http://localhost:9222/json/version

# 应该返回类似：
# {"Browser":"Chrome/XX.X.XXXX.XX", ...}
```

### 4. 配置环境变量

创建测试用的 `.env.test` 文件：

```bash
# 基础配置
API_KEY=test-key-12345
DATA_DIR=./data

# CDP 配置
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222

# 人类行为模拟配置（测试用，缩短时间）
CDP_HUMAN_BEHAVIOR_ENABLED=true
CDP_QUICK_MODE=false
CDP_WATCH_DURATION_MIN=5    # 测试时缩短为 5 秒
CDP_WATCH_DURATION_MAX=10   # 测试时缩短为 10 秒
CDP_PAGE_ALIVE_MIN=5        # 测试时缩短为 5 秒
CDP_PAGE_ALIVE_MAX=10       # 测试时缩短为 10 秒
CDP_SCROLL_PROBABILITY=0.8
CDP_PAUSE_PROBABILITY=0.2

# 重要：必须单并发
DOWNLOAD_CONCURRENCY=1
```

---

## 第二阶段：单元测试

### 测试 1：运行所有单元测试

```bash
pytest tests/test_cdp_human_behavior.py -v -s
```

**预期结果**：

```
tests/test_cdp_human_behavior.py::TestCleanupOldPages::test_cleanup_empty_context PASSED
tests/test_cdp_human_behavior.py::TestCleanupOldPages::test_cleanup_multiple_pages PASSED
tests/test_cdp_human_behavior.py::TestCleanupOldPages::test_cleanup_page_already_closed PASSED
tests/test_cdp_human_behavior.py::TestSleepWithPageCheck::test_sleep_full_duration PASSED
tests/test_cdp_human_behavior.py::TestSleepWithPageCheck::test_sleep_early_exit PASSED
tests/test_cdp_human_behavior.py::TestSimulateScroll::test_scroll_success PASSED
tests/test_cdp_human_behavior.py::TestSimulateScroll::test_scroll_page_closed PASSED
tests/test_cdp_human_behavior.py::TestSimulateScroll::test_scroll_error_handling PASSED
tests/test_cdp_human_behavior.py::TestSimulatePauseResume::test_pause_resume_success PASSED
tests/test_cdp_human_behavior.py::TestSimulatePauseResume::test_pause_resume_page_closed_early PASSED
tests/test_cdp_human_behavior.py::TestCookiesToNetscape::test_convert_valid_cookies PASSED
tests/test_cdp_human_behavior.py::TestCookiesToNetscape::test_filter_non_youtube_cookies PASSED
tests/test_cdp_human_behavior.py::TestBackgroundHumanBehavior::test_background_behavior_page_already_closed PASSED
tests/test_cdp_human_behavior.py::TestBackgroundHumanBehavior::test_background_behavior_page_closed_during_execution PASSED

================ 14 passed in 10.5s ================
```

### 测试 2：运行特定测试类

```bash
# 仅测试清理旧 Page
pytest tests/test_cdp_human_behavior.py::TestCleanupOldPages -v

# 仅测试滚动模拟
pytest tests/test_cdp_human_behavior.py::TestSimulateScroll -v

# 仅测试后台任务
pytest tests/test_cdp_human_behavior.py::TestBackgroundHumanBehavior -v
```

### 验证检查清单

- [ ] 所有单元测试通过
- [ ] 无异常或错误日志
- [ ] 测试覆盖率 > 80%（可选）

---

## 第三阶段：集成测试

### 测试 3：运行集成测试

**重要**：确保 Chrome 已启动并开启 CDP。

```bash
# 运行所有集成测试
pytest tests/test_cdp_human_behavior_integration.py -v -s -m integration

# 仅运行快速测试（跳过慢速测试）
pytest tests/test_cdp_human_behavior_integration.py -v -s -m "integration and not slow"
```

**预期输出**：

```
tests/test_cdp_human_behavior_integration.py::TestCDPHumanBehaviorIntegration::test_single_task_flow PASSED
tests/test_cdp_human_behavior_integration.py::TestCDPHumanBehaviorIntegration::test_quick_mode PASSED
tests/test_cdp_human_behavior_integration.py::TestCDPHumanBehaviorIntegration::test_disabled_human_behavior PASSED

================ 3 passed in 45.2s ================
```

### 测试 4：并发安全测试（慢速）

```bash
pytest tests/test_cdp_human_behavior_integration.py::test_concurrent_safety -v -s
```

**预期耗时**：约 2-3 分钟

**观察要点**：
- 任务 A 启动，打开视频页面
- 等待 10 秒
- 任务 B 启动，关闭任务 A 的页面
- 任务 B 的页面继续播放
- 所有任务成功

### 验证检查清单

- [ ] 单任务流程测试通过（主流程 < 15 秒）
- [ ] 快速模式测试通过（主流程 < 10 秒）
- [ ] 禁用模式测试通过
- [ ] 并发安全测试通过（如果运行）
- [ ] Cookie 文件在后台任务完成后被清理

---

## 第四阶段：手动测试（观察 Chrome 行为）

### 测试 5：单任务手动测试

**目的**：观察 Chrome 浏览器的实际行为，验证人类行为模拟是否生效。

```bash
python tests/test_cdp_human_behavior_manual.py
```

**选择选项 1**（单任务测试）

**观察清单**：

1. **页面打开** ✓
   - Chrome 窗口是否打开 YouTube 视频页面？

2. **视频播放** ✓
   - 视频是否开始播放（静音）？
   - 播放器是否显示正在播放状态？

3. **页面滚动** ✓
   - 页面是否向下滚动（约 50%-80% 页面高度）？
   - 滚动是否平滑？

4. **视频观看** ✓
   - 视频是否持续播放 5-10 秒（测试配置）？
   - 是否偶尔暂停/恢复（20% 概率）？

5. **页面存活** ✓
   - 页面是否在视频播放结束后保持打开（5-10 秒）？

6. **页面关闭** ✓
   - 页面是否最终自动关闭？

7. **主流程速度** ✓
   - 主流程是否在 10 秒内返回结果？
   - 下载是否成功？

### 测试 6：并发任务手动测试

**目的**：观察单 Page 策略，验证并发安全性。

```bash
python tests/test_cdp_human_behavior_manual.py
```

**选择选项 2**（并发任务测试）

**观察清单**：

1. **任务 A 启动** ✓
   - Chrome 打开第一个视频页面
   - 视频开始播放

2. **等待 10 秒** ✓
   - 第一个视频持续播放

3. **任务 B 启动** ✓
   - 第一个视频页面是否被关闭？（模拟人类关闭旧标签页）
   - 第二个视频页面是否打开？

4. **单 Page 验证** ✓
   - 任何时刻是否只有一个视频在播放？
   - 没有多个标签页同时播放？

5. **任务完成** ✓
   - 所有任务是否成功？
   - 无异常或错误？

### 验证检查清单

- [ ] 单任务测试：所有观察项通过
- [ ] 并发任务测试：单 Page 策略生效
- [ ] Chrome 行为完全像人类
- [ ] 无明显的机械化痕迹（快速打开关闭、重复访问等）

---

## 第五阶段：功能验证测试

### 测试 7：Cookie 文件清理验证

```bash
# 1. 启动服务
python -m src.main

# 2. 提交一个下载任务
curl -X POST http://localhost:8011/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key-12345" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=0kARDVL2nZg",
    "include_audio": true,
    "include_transcript": false
  }'

# 3. 立即检查临时文件
ls data/tmp/cdp_*.cookies.txt

# 4. 等待 15 秒后再次检查
sleep 15
ls data/tmp/cdp_*.cookies.txt

# 预期：Cookie 文件已被清理
```

### 测试 8：异常捕获验证

```bash
# 1. 关闭 Chrome（模拟连接失败）
# 手动关闭 Chrome 窗口

# 2. 提交下载任务
curl -X POST http://localhost:8011/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key-12345" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=0kARDVL2nZg",
    "include_audio": true
  }'

# 3. 查看日志
tail -f data/logs/app.log

# 预期：
# - 日志中有连接失败的错误（exc_info=True）
# - 任务自动降级到 ytdlp
# - 无未捕获的异常
```

### 测试 9：并发警告验证

```bash
# 1. 修改配置
export DOWNLOAD_CONCURRENCY=2

# 2. 启动服务
python -m src.main

# 3. 查看日志
tail data/logs/app.log

# 预期：
# - 日志中有警告：
#   "[cdp] CDP human behavior simulation requires DOWNLOAD_CONCURRENCY=1. ..."
```

### 验证检查清单

- [ ] Cookie 文件在后台任务完成后被清理
- [ ] 服务启动时清理过期的临时文件
- [ ] 异常被正确捕获并记录（exc_info=True）
- [ ] 并发 > 1 时有警告日志

---

## 第六阶段：性能验证

### 测试 10：主流程性能测试

**目的**：验证主流程速度不受影响。

```bash
# 运行性能测试（10 次重复）
for i in {1..10}; do
  echo "测试 $i:"
  time curl -X POST http://localhost:8011/api/v1/tasks \
    -H "Content-Type: application/json" \
    -H "X-API-Key: test-key-12345" \
    -d '{
      "video_url": "https://www.youtube.com/watch?v=0kARDVL2nZg",
      "include_audio": true,
      "include_transcript": false
    }'
  echo ""
  sleep 5
done
```

**预期结果**：

| 模式 | P50 | P95 | P99 |
|------|-----|-----|-----|
| 人类行为模拟启用 | < 8秒 | < 12秒 | < 15秒 |
| 快速模式 | < 6秒 | < 8秒 | < 10秒 |

### 测试 11：资源占用监控

```bash
# 1. 启动服务
python -m src.main

# 2. 监控 Chrome 进程
# Windows
tasklist | findstr chrome

# Linux/Mac
ps aux | grep chrome

# 3. 提交 3 个任务（间隔 10 秒）
# 观察 Chrome 内存占用

# 预期：
# - 任何时刻 Chrome 内存 < 500MB
# - 任何时刻只有 1 个视频页面
```

### 验证检查清单

- [ ] 主流程 P95 < 15 秒
- [ ] Chrome 内存峰值 < 500MB
- [ ] 任何时刻只有 1 个 Page

---

## 第七阶段：配置模式测试

### 测试 12：真实模式（生产配置）

修改 `.env`：

```bash
CDP_WATCH_DURATION_MIN=20
CDP_WATCH_DURATION_MAX=40
CDP_PAGE_ALIVE_MIN=30
CDP_PAGE_ALIVE_MAX=60
```

**运行测试**：

```bash
python tests/test_cdp_human_behavior_manual.py
# 选择选项 1
```

**观察**：
- 视频播放 20-40 秒
- 页面存活 30-60 秒
- 总时长 50-100 秒

### 测试 13：快速模式

修改 `.env`：

```bash
CDP_QUICK_MODE=true
```

**运行测试**：

```bash
pytest tests/test_cdp_human_behavior_integration.py::TestCDPHumanBehaviorIntegration::test_quick_mode -v -s
```

**验证**：
- 主流程 < 10 秒
- 无后台任务
- 页面立即关闭

### 测试 14：禁用模式

修改 `.env`：

```bash
CDP_HUMAN_BEHAVIOR_ENABLED=false
```

**运行测试**：

```bash
pytest tests/test_cdp_human_behavior_integration.py::TestCDPHumanBehaviorIntegration::test_disabled_human_behavior -v -s
```

**验证**：
- 主流程正常工作
- 无人类行为模拟
- 无后台任务

### 验证检查清单

- [ ] 真实模式：行为时长符合配置
- [ ] 快速模式：无后台任务
- [ ] 禁用模式：功能正常

---

## 第八阶段：回归测试

### 测试 15：确保现有功能未受影响

```bash
# 运行所有现有的 CDP 测试
pytest tests/test_cdp*.py -v

# 预期：所有测试通过
```

### 测试 16：完整的下载流程

```bash
# 测试音频下载
pytest tests/test_download_modes.py -v

# 测试降级机制
pytest tests/test_403_stop_fallback.py -v
```

### 验证检查清单

- [ ] 所有现有 CDP 测试通过
- [ ] 下载流程正常
- [ ] 降级机制正常

---

## 测试结果记录

### 单元测试结果

| 测试类 | 通过/总数 | 状态 |
|--------|----------|------|
| TestCleanupOldPages | /3 | |
| TestSleepWithPageCheck | /2 | |
| TestSimulateScroll | /3 | |
| TestSimulatePauseResume | /2 | |
| TestCookiesToNetscape | /2 | |
| TestBackgroundHumanBehavior | /2 | |
| **总计** | **/14** | |

### 集成测试结果

| 测试 | 主流程耗时 | 状态 |
|------|-----------|------|
| test_single_task_flow | 秒 | |
| test_quick_mode | 秒 | |
| test_disabled_human_behavior | 秒 | |
| test_concurrent_safety | 秒 | |

### 手动测试结果

| 观察项 | 状态 | 备注 |
|--------|------|------|
| 页面打开 | | |
| 视频播放 | | |
| 页面滚动 | | |
| 视频观看 | | |
| 页面存活 | | |
| 页面关闭 | | |
| 单 Page 策略 | | |

### 功能验证结果

| 功能 | 状态 | 备注 |
|------|------|------|
| Cookie 清理 | | |
| 异常捕获 | | |
| 并发警告 | | |
| 临时文件清理 | | |

---

## 常见问题排查

### Q1: Chrome 连接失败

**症状**：

```
[cdp] Failed to connect to browser: connect ECONNREFUSED 127.0.0.1:9222
```

**解决**：

1. 确认 Chrome 已启动：`curl http://localhost:9222/json/version`
2. 检查端口是否被占用：`netstat -an | grep 9222`
3. 重启 Chrome

### Q2: 单元测试失败（Playwright 未安装）

**症状**：

```
Skipped: Playwright is required for CDP tests
```

**解决**：

```bash
pip install playwright
```

### Q3: Cookie 文件未清理

**症状**：

```bash
ls data/tmp/cdp_*.cookies.txt
# 显示多个文件
```

**解决**：

1. 检查后台任务是否启动：查看日志
2. 手动清理：`rm data/tmp/cdp_*.cookies.txt`
3. 重启服务（会自动清理过期文件）

### Q4: 后台任务异常

**症状**：

```
[cdp] Background behavior task failed for XXX
```

**解决**：

1. 查看完整日志（`exc_info=True`）
2. 检查 Page 是否被提前关闭
3. 确认配置参数有效

---

## 下一步计划

### 本地测试完成后

- [ ] 更新 `IMPLEMENTATION_SUMMARY.md`
- [ ] 记录测试结果
- [ ] 识别并修复问题

### 灰度测试准备

- [ ] 配置 A/B 测试（50% 启用，50% 禁用）
- [ ] 设置监控指标（403 错误率、下载成功率）
- [ ] 准备回滚方案

### 全量上线准备

- [ ] 灰度测试成功率 > 95%
- [ ] 403 错误率下降 > 20%
- [ ] 无资源泄漏、异常
- [ ] 更新生产配置

---

**文档结束**
