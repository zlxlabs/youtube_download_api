# CDP 下载器人类行为模拟设计方案

## 文档信息

- **版本**: v1.1
- **创建日期**: 2026-01-29
- **最后更新**: 2026-01-29
- **状态**: 设计阶段（已评估）
- **可行性**: ★★★★☆ (4/5) - 可行，需修复关键问题

## 1. 需求背景

### 1.1 当前问题

CDP 下载器虽然可以正常工作，但浏览器行为过于机械化，容易被 YouTube 识别为自动化工具：

**问题表现**：
1. **快速打开关闭**：打开视频页面 → 获取数据 → 立即关闭（< 5秒）
2. **重复访问**：两次打开同一视频页面
   - 第一次：导出 cookies → 关闭
   - 第二次：提取 headers → 关闭
3. **无人类行为**：没有滚动、停留、观看等正常浏览行为
4. **固定模式**：每次操作时间固定，无随机性

### 1.2 风险评估

- ❌ **账号封禁风险**：YouTube 可能识别并封禁使用的账号
- ❌ **IP 限流风险**：触发 IP 级别的访问限制
- ❌ **403 错误率高**：机械化行为导致频繁被拒绝访问

### 1.3 优化目标

- ✅ **降低风控概率**：浏览器行为完全像正常人类
- ✅ **保持下载速度**：主流程速度不受影响（~5秒返回数据）
- ✅ **提高成功率**：减少 403 错误，提高下载稳定性
- ✅ **账号安全**：保护 YouTube 账号不被封禁

---

## 2. 可行性评估与关键问题

### 2.1 总体评估

**可行性评分：★★★★☆ (4/5)**

方案**总体可行**，技术路线清晰，主流程与后台任务分离的思路合理。

**优点**：
- ✅ 主流程速度不受影响（~5秒）
- ✅ 并发安全性良好（单并发场景）
- ✅ 资源占用可控（单 Page 策略）
- ✅ 降级灵活（支持快速禁用）

**风险**：
- ⚠️ 需要修复 3 个高优先级问题（详见 2.2 节）
- ⚠️ 依赖单并发假设（DOWNLOAD_CONCURRENCY=1）
- ⚠️ 风控效果需验证（建议 A/B 测试）

### 2.2 关键问题与修复建议

#### 🔴 问题 1：Cookie 文件清理逻辑冲突（必须修复）

**问题描述**：
- **当前代码**（cdp_downloader.py:376-380）：主流程 finally 块清理 cookie 文件
- **设计方案**（3.2.1）：主流程 finally 块**不清理** cookie 文件，由后台任务清理
- **冲突**：如果主流程出错（未启动后台任务），cookie 文件不会被清理

**修复方案**：
```python
# 主流程 finally 块检查后台任务是否启动
finally:
    if not context_is_reused:
        await context.close()

    # 如果后台任务未启动（快速模式或异常），立即清理
    if not background_task_started:
        cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
        if cookie_file.exists():
            cookie_file.unlink()
```

**兜底机制**：
- 在 `src/main.py` 的 `lifespan()` 启动事件中添加清理逻辑
- 清理超过 1 小时的临时 cookie 文件（`cdp_*.cookies.txt`）

---

#### 🔴 问题 2：后台任务异常处理不完善（必须修复）

**问题描述**：
设计方案使用 `asyncio.create_task()` 启动后台任务，但未捕获异常：

```python
asyncio.create_task(
    self._background_human_behavior(page, video_url, video_id, task_id)
)
# ← Task 对象未保存，异常会被吞掉（Silent Failure）
```

**后果**：
- 后台任务抛出异常 → 无法在日志中看到
- 服务关闭时 → 后台任务被强制取消 → 资源未清理

**修复方案**：
```python
# 添加异常处理回调
if self.settings.cdp_human_behavior_enabled and not self.settings.cdp_quick_mode:
    task = asyncio.create_task(
        self._background_human_behavior(page, video_url, video_id, task_id)
    )

    # 添加异常处理回调
    def handle_task_exception(t):
        try:
            t.result()
        except Exception as e:
            logger.error(
                f"[cdp] Background behavior task failed for {video_id}: {e}",
                exc_info=True
            )

    task.add_done_callback(handle_task_exception)
    background_task_started = True  # ← 设置标志（用于 Cookie 清理）
```

---

#### 🔴 问题 3：并发安全性依赖单并发假设（必须修复）

**问题描述**：
设计方案假设任务是**串行执行**的，但当前代码没有强制限制。

**并发冲突场景**：
如果用户将 `DOWNLOAD_CONCURRENCY` 改为 2：

```
时刻 00:00 - 任务 A 开始 → Page A 播放中
时刻 00:02 - 任务 B 开始（并发）
            → 清理旧 Page（关闭 Page A）
            → 任务 A 后台任务提前终止（仅播放 2 秒，违背设计）
```

**修复方案**：
```python
def __init__(self, settings: Settings):
    # ... 现有代码 ...

    # 警告：人类行为模拟要求单并发
    if (
        self.settings.cdp_human_behavior_enabled
        and self.settings.download_concurrency > 1
    ):
        logger.warning(
            "[cdp] CDP human behavior simulation requires DOWNLOAD_CONCURRENCY=1. "
            "Concurrent tasks may interfere with each other, causing background "
            "behaviors to terminate early. Please set DOWNLOAD_CONCURRENCY=1 "
            "or disable human behavior (CDP_HUMAN_BEHAVIOR_ENABLED=false)."
        )
```

**文档说明**：
在 README.md 的 CDP 配置章节中明确说明：
> **重要**：启用人类行为模拟时，必须设置 `DOWNLOAD_CONCURRENCY=1`，否则并发任务会相互干扰。

---

### 2.3 实施优先级

#### 🔴 必须修复（阻塞上线）

1. **修复 Cookie 文件清理逻辑**
   - 主流程添加 `background_task_started` 标志
   - 仅在后台任务未启动时清理
   - 添加服务启动时清理过期文件的逻辑（main.py）

2. **添加后台任务异常处理**
   - 使用 `task.add_done_callback()` 捕获异常
   - 记录到日志（`exc_info=True`）

3. **文档化并发要求**
   - 在代码中添加警告日志
   - 在 README.md 中明确说明并发限制

#### 🟡 建议修复（上线前完成）

4. **添加资源清理机制**
   - 服务启动时清理临时文件（`main.py`）
   - Browser 重连时清理旧 Page（可选）

5. **添加监控指标**
   - 记录后台任务完成率、执行时长
   - 为 A/B 测试做准备

---

## 3. 解决方案概述

### 3.1 核心思路

**主流程与后台任务分离**：

```
主流程（快速返回）：
  打开页面
  → 快速获取 cookies + headers（2-3秒）
  → 立即返回数据 ✅（不阻塞下载）

后台任务（异步执行）：
  → 模拟人类行为（滚动、观看、停留）
  → 保持页面存活（30-60秒）
  → 关闭页面
```

**关键优势**：
- ✅ 下载速度不受影响（数据获取后立即返回）
- ✅ 浏览器行为完全像人类（后台持续播放）
- ✅ 服务器资源充足，可支持后台任务

### 3.2 并发安全策略

**单 Page 策略（模拟人类关闭旧标签页）**：

```
任务 A (00:00):
  → 创建 Page A
  → 获取数据，返回
  → Page A 后台播放...

任务 B (00:10):
  → 检测到 Page A 还在
  → 关闭 Page A（模拟人类关闭旧标签页）✅
  → 创建 Page B
  → 获取数据，返回
  → Page B 后台播放...
```

**效果**：任何时刻只有一个视频在播放，完全符合人类行为。

---

## 4. 技术实现

### 4.1 配置项设计

#### 4.1.1 新增环境变量

```bash
# ========== CDP 人类行为模拟配置 ==========

# 总开关
CDP_HUMAN_BEHAVIOR_ENABLED=true

# 快速模式（跳过人类行为，用于测试）
CDP_QUICK_MODE=false

# 观看时长（秒，真实模式）
CDP_WATCH_DURATION_MIN=20
CDP_WATCH_DURATION_MAX=40

# 页面存活时长（秒）
CDP_PAGE_ALIVE_MIN=30
CDP_PAGE_ALIVE_MAX=60

# 行为概率（0.0 - 1.0）
CDP_SCROLL_PROBABILITY=0.8   # 80% 概率滚动
CDP_PAUSE_PROBABILITY=0.2    # 20% 概率暂停/恢复
```

#### 4.1.2 配置类定义

```python
# src/config.py

class Settings(BaseSettings):
    # ... 现有配置 ...

    # ========== CDP 人类行为模拟配置 ==========
    cdp_human_behavior_enabled: bool = Field(
        default=True,
        description="启用 CDP 人类行为模拟（降低风控）"
    )

    cdp_quick_mode: bool = Field(
        default=False,
        description="快速模式：跳过人类行为模拟（用于测试）"
    )

    cdp_watch_duration_min: int = Field(
        default=20,
        description="视频观看最小时长（秒）"
    )
    cdp_watch_duration_max: int = Field(
        default=40,
        description="视频观看最大时长（秒）"
    )

    cdp_page_alive_min: int = Field(
        default=30,
        description="页面关闭前最小存活时长（秒）"
    )
    cdp_page_alive_max: int = Field(
        default=60,
        description="页面关闭前最大存活时长（秒）"
    )

    cdp_scroll_probability: float = Field(
        default=0.8,
        description="滚动页面的概率"
    )
    cdp_pause_probability: float = Field(
        default=0.2,
        description="暂停/恢复视频的概率"
    )
```

### 4.2 核心代码改造

#### 4.2.1 主流程改造

**文件**: `src/downloaders/cdp_downloader.py`

**方法**: `download_resources()`

**改动点**：
1. 添加 `background_task_started` 标志（用于 Cookie 清理判断）
2. 添加 `_cleanup_old_pages()` 清理旧 Page
3. 使用 `_quick_fetch_data()` 快速获取数据
4. 启动后台任务 `_background_human_behavior()`（异步，不阻塞）
5. 添加 `task.add_done_callback()` 捕获异常
6. 主流程立即返回结果
7. finally 块根据 `background_task_started` 标志决定是否清理 Cookie

```python
async def download_resources(self, ...) -> DownloaderResult:
    """下载资源（主流程）"""

    task_id = f"cdp_{video_id}_{int(time.time())}"
    background_task_started = False  # ← 新增：标志后台任务是否启动

    try:
        # 1. 获取 Browser + Context
        browser, cdp_url = await self._get_browser()

        # 2. 复用或创建 Context
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context(...)

        try:
            # 3. 清理旧 Page（模拟人类关闭旧标签页）
            await self._cleanup_old_pages(context)

            # 4. 快速获取数据（创建新 Page）
            page, cookie_file, headers = await self._quick_fetch_data(
                context, video_url, video_id, task_id
            )

            # 5. 启动后台任务（异步，不阻塞）
            if self.settings.cdp_human_behavior_enabled and not self.settings.cdp_quick_mode:
                task = asyncio.create_task(
                    self._background_human_behavior(page, video_url, video_id, task_id)
                )

                # ← 新增：添加异常处理回调
                def handle_task_exception(t):
                    try:
                        t.result()
                    except Exception as e:
                        logger.error(
                            f"[cdp] Background behavior task failed for {video_id}: {e}",
                            exc_info=True
                        )

                task.add_done_callback(handle_task_exception)
                background_task_started = True  # ← 新增：标记后台任务已启动
            else:
                # 快速模式：直接关闭页面
                await page.close()

            # 6. 提取音频 URL
            audio_info = await self._extract_audio_url(...)

            # 7. 下载音频
            audio_path = await self._download_audio(...)

            # 8. 立即返回结果（不等待后台任务）
            return DownloaderResult(...)

        finally:
            # 不关闭 Context（保持复用）

            # ← 修改：仅在后台任务未启动时清理 Cookie
            if not background_task_started:
                cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
                if cookie_file.exists():
                    cookie_file.unlink()
                    logger.debug(f"[cdp] Cleaned up cookie file: {cookie_file}")

    except Exception as e:
        await self._update_circuit_breaker(success=False)
        await self._handle_download_error(e, video_id, task_id)
        raise
```

#### 4.2.2 新增方法：清理旧 Page

```python
async def _cleanup_old_pages(self, context: BrowserContext) -> None:
    """
    清理旧的 Page（模拟人类关闭旧标签页）。

    并发安全：
    - 新任务开始时，关闭所有旧的 Page
    - 旧任务的后台任务会自动检测 Page 关闭并退出
    - 确保任何时刻只有一个视频在播放

    人类行为模拟：
    - 就像人类关闭旧标签页再打开新标签页
    - 不会同时播放多个视频
    """
    if not context.pages:
        logger.debug("[cdp] No old pages to clean up")
        return

    old_pages = list(context.pages)
    logger.info(
        f"[cdp] Cleaning up {len(old_pages)} old page(s) "
        "(simulating closing old tabs)"
    )

    for i, page in enumerate(old_pages):
        try:
            page_url = page.url if not page.is_closed() else "unknown"
            await page.close()
            logger.debug(f"[cdp] Closed old page {i+1}/{len(old_pages)}: {page_url}")
        except Exception as e:
            logger.debug(f"[cdp] Failed to close old page {i+1}: {e}")
```

#### 4.2.3 新增方法：快速数据获取

```python
async def _quick_fetch_data(
    self,
    context: BrowserContext,
    video_url: str,
    video_id: str,
    task_id: str,
) -> Tuple[Page, Path, Dict[str, str]]:
    """
    快速获取 cookies + headers（2-3秒完成）。

    合并原有的两次页面访问：
    - 原：_export_cookies() + _extract_request_headers()
    - 新：一次访问完成所有数据获取

    并发安全：
    - 总是创建新 Page（旧 Page 已被清理）

    Returns:
        (page, cookie_file, headers)
        - page: 保持打开状态，供后台任务使用
        - cookie_file: cookies 文件路径
        - headers: 真实请求 headers
    """
    logger.info(f"[cdp] Quick fetching data for {video_id} (task: {task_id})")

    # 总是创建新 Page
    page = await context.new_page()
    logger.debug(f"[cdp] Created new page for task {task_id}")

    try:
        # 访问视频页面
        await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)

        # 设置请求拦截器（捕获 headers）
        captured_headers = {}
        headers_captured = asyncio.Event()

        async def capture_request(request):
            if "googlevideo.com" in request.url:
                nonlocal captured_headers
                captured_headers = request.headers
                headers_captured.set()

        page.on("request", capture_request)

        # 等待视频播放器加载
        try:
            await page.wait_for_selector("#movie_player", timeout=10000)
        except Exception:
            logger.warning("[cdp] Video player not found, continuing anyway")

        # 触发视频播放（捕获 headers）
        try:
            await page.evaluate("""() => {
                const video = document.querySelector('video');
                if (!video) return;
                video.muted = true;
                const p = video.play();
                if (p && p.catch) p.catch(() => {});
            }""")

            # 等待捕获 headers（最多 3 秒）
            await asyncio.wait_for(headers_captured.wait(), timeout=3)
            logger.debug(f"[cdp] Captured {len(captured_headers)} headers")
        except asyncio.TimeoutError:
            logger.warning("[cdp] Failed to capture headers quickly, using defaults")

        # 使用 CDP 获取 cookies
        cdp_session = await context.new_cdp_session(page)
        cookies_result = await cdp_session.send("Network.getAllCookies")
        cookies = cookies_result.get("cookies", [])

        # 写入 cookie 文件
        cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(self._cookies_to_netscape(cookies), encoding="utf-8")

        # 返回结果（保持 page 打开）
        headers = captured_headers or {
            "referer": "https://www.youtube.com/",
            "origin": "https://www.youtube.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        logger.info(
            f"[cdp] Quick fetch completed for {task_id}: "
            f"{len(cookies)} cookies, {len(headers)} headers"
        )
        return page, cookie_file, headers

    except Exception as e:
        # 出错时立即关闭 Page
        try:
            await page.close()
        except Exception:
            pass
        raise
```

#### 4.2.4 新增方法：后台人类行为模拟

```python
async def _background_human_behavior(
    self,
    page: Page,
    video_url: str,
    video_id: str,
    task_id: str,
) -> None:
    """
    后台异步执行：人类行为模拟（不阻塞主流程）。

    并发安全：
    - Page 可能被新任务提前关闭（_cleanup_old_pages）
    - 检测到 Page 关闭时，静默退出

    流程：
    1. 检查 Page 是否已关闭
    2. 滚动页面（模拟浏览，80% 概率）
    3. 观看视频片段（20-40秒随机）
    4. 可能暂停/恢复（20% 概率）
    5. 保持页面存活（30-60秒随机）
    6. 清理 cookie 文件
    7. 关闭页面
    """
    try:
        # 检查 Page 是否已关闭
        if page.is_closed():
            logger.debug(
                f"[cdp] Page already closed for {video_id} (task: {task_id}), "
                "skipping background behavior"
            )
            return

        logger.info(f"[cdp] Starting background human behavior for {video_id}")

        # 1. 随机等待（模拟人类反应时间）
        await asyncio.sleep(random.uniform(1, 2))

        # 再次检查（可能在等待期间被关闭）
        if page.is_closed():
            logger.debug(f"[cdp] Page closed during initial wait for {video_id}")
            return

        # 2. 滚动页面（80% 概率）
        if random.random() < self.settings.cdp_scroll_probability:
            await self._simulate_scroll(page)

        # 3. 观看视频
        watch_duration = random.uniform(
            self.settings.cdp_watch_duration_min,
            self.settings.cdp_watch_duration_max
        )
        logger.debug(f"[cdp] Watching video for {watch_duration:.1f}s")

        # 观看期间可能暂停/恢复
        if random.random() < self.settings.cdp_pause_probability:
            await self._simulate_pause_resume(page, watch_duration)
        else:
            # 分段睡眠，定期检查 Page 状态
            await self._sleep_with_page_check(page, watch_duration, video_id)

        # 4. 保持页面存活一段时间
        alive_duration = random.uniform(
            self.settings.cdp_page_alive_min,
            self.settings.cdp_page_alive_max
        )
        logger.debug(f"[cdp] Keeping page alive for {alive_duration:.1f}s")
        await self._sleep_with_page_check(page, alive_duration, video_id)

        logger.info(
            f"[cdp] Background behavior completed for {video_id}: "
            f"watched={watch_duration:.1f}s, alive={alive_duration:.1f}s"
        )

    except Exception as e:
        # 检查是否是 Page 关闭导致的错误
        error_msg = str(e).lower()
        if "page has been closed" in error_msg or "target closed" in error_msg:
            logger.debug(
                f"[cdp] Page closed during background behavior for {video_id} "
                "(likely cleaned up by new task)"
            )
            return

        logger.warning(f"[cdp] Background behavior error for {video_id}: {e}")

    finally:
        # 清理 cookie 文件
        cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
        if cookie_file.exists():
            try:
                cookie_file.unlink()
                logger.debug(f"[cdp] Cleaned up cookie file: {cookie_file}")
            except Exception as e:
                logger.warning(f"[cdp] Failed to clean cookie file: {e}")

        # 关闭页面（如果还没被关闭）
        try:
            if not page.is_closed():
                await page.close()
                logger.debug(f"[cdp] Closed page for {video_id}")
        except Exception as e:
            logger.debug(f"[cdp] Failed to close page (already closed?): {e}")
```

#### 4.2.5 新增方法：分段睡眠（检查 Page 状态）

```python
async def _sleep_with_page_check(
    self,
    page: Page,
    duration: float,
    video_id: str,
    check_interval: float = 5.0
) -> None:
    """
    分段睡眠，定期检查 Page 是否被关闭。

    用途：
    - 在长时间等待期间，定期检查 Page 状态
    - 如果 Page 被新任务关闭，立即退出（不浪费时间）

    Args:
        page: Page 对象
        duration: 总睡眠时长（秒）
        video_id: 视频 ID（用于日志）
        check_interval: 检查间隔（秒，默认 5 秒）
    """
    elapsed = 0.0

    while elapsed < duration:
        # 检查 Page 是否已关闭
        if page.is_closed():
            logger.debug(
                f"[cdp] Page closed during sleep for {video_id} "
                f"(elapsed: {elapsed:.1f}s/{duration:.1f}s)"
            )
            return

        # 睡眠一小段时间
        sleep_time = min(check_interval, duration - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time
```

#### 4.2.6 新增方法：模拟滚动

```python
async def _simulate_scroll(self, page: Page) -> None:
    """
    模拟页面滚动（简单版）。

    人类行为：
    - 随机滚动距离（50%-80% 页面高度）
    - 平滑滚动（smooth）
    - 等待滚动动画完成
    """
    try:
        if page.is_closed():
            return

        # 随机滚动距离（50%-80% 页面高度）
        scroll_ratio = random.uniform(0.5, 0.8)

        await page.evaluate(f"""() => {{
            const scrollHeight = document.documentElement.scrollHeight;
            const targetY = scrollHeight * {scroll_ratio};

            // 平滑滚动
            window.scrollTo({{
                top: targetY,
                behavior: 'smooth'
            }});
        }}""")

        # 等待滚动动画完成
        await asyncio.sleep(random.uniform(1, 2))

        logger.debug(f"[cdp] Scrolled page: {scroll_ratio:.0%}")

    except Exception as e:
        error_msg = str(e).lower()
        if "page has been closed" not in error_msg and "target closed" not in error_msg:
            logger.debug(f"[cdp] Scroll failed: {e}")
```

#### 4.2.7 新增方法：模拟暂停/恢复

```python
async def _simulate_pause_resume(self, page: Page, duration: float) -> None:
    """
    模拟暂停/恢复视频。

    人类行为：
    - 观看一半时间后暂停
    - 暂停 2-5 秒（模拟查看其他内容）
    - 恢复播放
    - 继续观看剩余时间
    """
    try:
        if page.is_closed():
            return

        # 观看一半时间
        await self._sleep_with_page_check(page, duration * 0.5, "pause_resume")

        if page.is_closed():
            return

        # 暂停视频
        await page.evaluate("""() => {
            const video = document.querySelector('video');
            if (video && !video.paused) {
                video.pause();
            }
        }""")
        logger.debug("[cdp] Paused video")

        # 暂停 2-5 秒
        pause_duration = random.uniform(2, 5)
        await self._sleep_with_page_check(page, pause_duration, "pause_resume")

        if page.is_closed():
            return

        # 恢复播放
        await page.evaluate("""() => {
            const video = document.querySelector('video');
            if (video && video.paused) {
                const p = video.play();
                if (p && p.catch) p.catch(() => {});
            }
        }""")
        logger.debug("[cdp] Resumed video")

        # 继续观看剩余时间
        await self._sleep_with_page_check(page, duration * 0.5, "pause_resume")

    except Exception as e:
        error_msg = str(e).lower()
        if "page has been closed" not in error_msg and "target closed" not in error_msg:
            logger.debug(f"[cdp] Pause/resume failed: {e}")
            # 失败则正常等待
            await self._sleep_with_page_check(page, duration, "pause_resume")
```

#### 4.2.8 移除的方法

以下方法将被合并或移除：

- ❌ `_export_cookies()` - 合并到 `_quick_fetch_data()`
- ❌ `_extract_request_headers()` - 合并到 `_quick_fetch_data()`

---

## 5. 并发安全性分析

### 5.1 并发场景

#### 场景 1：单任务（正常流程）

```
00:00 - 任务 A 开始
        → 无旧 Page，创建 Page A
        → 快速获取数据（3秒）
        → 返回结果 ✅
        → Page A 后台播放（50-100秒）

01:00 - Page A 后台任务完成
        → 清理 cookie 文件
        → 关闭 Page A
```

**结果**：
- ✅ 主流程 3 秒返回
- ✅ 浏览器行为持续 50-100 秒
- ✅ 完全符合人类行为

#### 场景 2：任务并发（多任务顺序到达）

```
00:00 - 任务 A 开始
        → 创建 Page A
        → 返回数据
        → Page A 后台播放...

00:10 - 任务 B 开始（Page A 还在播放）
        → 检测到 Page A
        → 关闭 Page A ✅
        → 任务 A 后台任务检测到关闭，静默退出 ✅
        → 创建 Page B
        → 返回数据
        → Page B 后台播放...

00:20 - 任务 C 开始（Page B 还在播放）
        → 检测到 Page B
        → 关闭 Page B
        → 创建 Page C
        → ...
```

**结果**：
- ✅ 任何时刻只有一个视频在播放
- ✅ 旧任务自动退出，无资源泄漏
- ✅ 完全符合人类行为（关闭旧标签页）

#### 场景 3：任务快速连续

```
00:00 - 任务 A 开始
        → 创建 Page A
        → 返回数据（00:03）
        → Page A 后台任务启动...

00:05 - 任务 B 开始（Page A 刚播放 2 秒）
        → 关闭 Page A
        → 任务 A 后台任务退出（仅执行 2 秒）✅
        → 创建 Page B
        → ...
```

**结果**：
- ✅ 旧任务提前终止，节省资源
- ✅ 新任务正常执行

### 5.2 资源占用分析

| 场景 | Page 数量 | 后台任务数量 | Chrome 内存 |
|------|----------|------------|------------|
| 空闲 | 0 | 0 | 基线 |
| 单任务 | 1 | 1 | 基线 +100MB |
| 任务并发（被清理）| 1 | 1 | 基线 +100MB |
| 最坏情况（10任务/分钟）| 1 | 1 | 基线 +100MB |

**结论**：任何时刻最多 1 个 Page + 1 个后台任务，资源占用最优。

---

## 6. 配置说明

### 6.1 生产环境配置（推荐）

```bash
# .env

# ========== CDP 人类行为模拟（推荐开启）==========
CDP_HUMAN_BEHAVIOR_ENABLED=true
CDP_QUICK_MODE=false

# 观看时长（真实模式：20-40秒）
CDP_WATCH_DURATION_MIN=20
CDP_WATCH_DURATION_MAX=40

# 页面存活时长（30-60秒）
CDP_PAGE_ALIVE_MIN=30
CDP_PAGE_ALIVE_MAX=60

# 行为概率
CDP_SCROLL_PROBABILITY=0.8   # 80% 概率滚动
CDP_PAUSE_PROBABILITY=0.2    # 20% 概率暂停/恢复
```

**特点**：
- ✅ 最大程度模拟人类
- ✅ 降低风控概率
- ✅ 保护账号安全
- ⚠️ Chrome 资源占用增加（每任务 +100MB，持续 50-100秒）

### 6.2 测试环境配置

```bash
# ========== 快速模式（跳过人类行为）==========
CDP_HUMAN_BEHAVIOR_ENABLED=false
# 或者
CDP_QUICK_MODE=true
```

**特点**：
- ✅ 主流程速度不变（~5秒）
- ✅ 无后台任务，资源占用最小
- ❌ 不模拟人类行为
- ❌ 风控风险较高（仅用于测试）

### 6.3 平衡模式配置

```bash
# ========== 平衡模式（缩短时间）==========
CDP_HUMAN_BEHAVIOR_ENABLED=true
CDP_QUICK_MODE=false

# 观看时长（10-20秒，轻度人类化）
CDP_WATCH_DURATION_MIN=10
CDP_WATCH_DURATION_MAX=20

# 页面存活时长（15-30秒）
CDP_PAGE_ALIVE_MIN=15
CDP_PAGE_ALIVE_MAX=30

# 行为概率
CDP_SCROLL_PROBABILITY=0.5   # 50% 概率滚动
CDP_PAUSE_PROBABILITY=0.1    # 10% 概率暂停/恢复
```

**特点**：
- ✅ 有一定人类行为（比快速模式好）
- ✅ 资源占用较小（总时长 25-50秒）
- ⚠️ 风控保护低于真实模式

---

## 7. 性能影响分析

### 7.1 主流程性能

| 阶段 | 当前耗时 | 优化后耗时 | 变化 |
|------|---------|-----------|------|
| 打开页面 | 1秒 | 1秒 | 无变化 |
| 导出 cookies | 1秒 | - | 合并 |
| 提取 headers | 1秒 | - | 合并 |
| 快速获取数据 | - | 2-3秒 | 新增 |
| 提取音频 URL | 1秒 | 1秒 | 无变化 |
| 下载音频 | 1秒 | 1秒 | 无变化 |
| **总耗时** | **~5秒** | **~5秒** | **无变化** ✅ |

**结论**：主流程速度不受影响。

### 7.2 Chrome 行为时长

| 模式 | 当前时长 | 优化后时长 | 变化 |
|------|---------|-----------|------|
| 快速模式 | 5秒 | 5秒 | 无变化 |
| 平衡模式 | - | 25-50秒 | 新增 |
| 真实模式 | - | 50-100秒 | 新增 ✅ |

**结论**：Chrome 行为时长大幅延长，完全符合人类。

### 7.3 资源占用

| 指标 | 当前 | 优化后（真实模式）| 变化 |
|------|-----|----------------|------|
| 单任务 Chrome 内存 | +100MB | +100MB | 无变化 |
| 并发任务 Chrome 内存 | +300MB | +100MB | **减少** ✅ |
| 后台任务数量 | 0 | 1 | +1 |
| Page 数量（峰值）| 3 | 1 | **减少** ✅ |

**结论**：并发场景下资源占用反而更少（单 Page 策略）。

---

## 8. 测试验证

### 8.1 单元测试

#### 测试 1：快速数据获取

```python
async def test_quick_fetch_data():
    """测试快速获取 cookies + headers"""
    downloader = CDPDownloader(settings)

    # 模拟 Context
    context = await browser.new_context()

    # 调用
    page, cookie_file, headers = await downloader._quick_fetch_data(
        context, video_url, video_id, task_id
    )

    # 验证
    assert page is not None
    assert not page.is_closed()
    assert cookie_file.exists()
    assert len(headers) > 0

    # 清理
    await page.close()
```

#### 测试 2：清理旧 Page

```python
async def test_cleanup_old_pages():
    """测试清理旧 Page"""
    downloader = CDPDownloader(settings)
    context = await browser.new_context()

    # 创建 3 个 Page
    page1 = await context.new_page()
    page2 = await context.new_page()
    page3 = await context.new_page()

    assert len(context.pages) == 3

    # 清理
    await downloader._cleanup_old_pages(context)

    # 验证：所有 Page 都被关闭
    assert len(context.pages) == 0
    assert page1.is_closed()
    assert page2.is_closed()
    assert page3.is_closed()
```

#### 测试 3：后台任务（Page 提前关闭）

```python
async def test_background_behavior_page_closed():
    """测试后台任务检测 Page 关闭"""
    downloader = CDPDownloader(settings)
    context = await browser.new_context()
    page = await context.new_page()

    # 启动后台任务
    task = asyncio.create_task(
        downloader._background_human_behavior(page, video_url, video_id, task_id)
    )

    # 等待 2 秒后关闭 Page（模拟新任务）
    await asyncio.sleep(2)
    await page.close()

    # 等待后台任务完成（应该检测到关闭并退出）
    await task

    # 验证：后台任务正常退出，无异常
    assert task.done()
```

### 8.2 集成测试

#### 测试 4：单任务完整流程

```python
async def test_single_task_flow():
    """测试单任务完整流程"""
    downloader = CDPDownloader(settings)

    # 下载资源
    start = time.time()
    result = await downloader.download_resources(
        video_url=video_url,
        video_id=video_id,
        output_dir=output_dir,
        include_audio=True,
        include_transcript=False,
    )
    elapsed = time.time() - start

    # 验证：主流程 < 10秒
    assert elapsed < 10
    assert result.success
    assert result.audio_path.exists()

    # 等待后台任务（应该在 50-100 秒内完成）
    await asyncio.sleep(110)

    # 验证：cookie 文件已清理
    cookie_file = settings.data_dir / "tmp" / f"cdp_{video_id}_*.cookies.txt"
    assert not any(cookie_file.parent.glob(f"cdp_{video_id}_*.cookies.txt"))
```

#### 测试 5：任务并发

```python
async def test_concurrent_tasks():
    """测试任务并发（单 Page 策略）"""
    downloader = CDPDownloader(settings)

    # 启动 3 个任务（间隔 10 秒）
    tasks = []
    for i in range(3):
        task = asyncio.create_task(
            downloader.download_resources(...)
        )
        tasks.append(task)
        await asyncio.sleep(10)

    # 等待所有任务完成
    results = await asyncio.gather(*tasks)

    # 验证：所有任务成功
    assert all(r.success for r in results)

    # 验证：任何时刻只有 1 个 Page
    browser = downloader._browser
    if browser and browser.contexts:
        context = browser.contexts[0]
        assert len(context.pages) <= 1
```

### 8.3 手动测试

#### 测试 6：观察 Chrome 行为

1. 启动 Chrome CDP：
   ```bash
   chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp
   ```

2. 配置环境变量：
   ```bash
   CDP_HUMAN_BEHAVIOR_ENABLED=true
   CDP_WATCH_DURATION_MIN=20
   CDP_WATCH_DURATION_MAX=40
   ```

3. 提交下载任务

4. 观察 Chrome 窗口：
   - ✅ 视频页面打开
   - ✅ 视频开始播放（静音）
   - ✅ 页面滚动
   - ✅ 视频持续播放 20-40 秒
   - ✅ 页面保持存活 30-60 秒
   - ✅ 页面关闭

5. 提交第二个任务（第一个任务还在播放时）：
   - ✅ 旧标签页关闭
   - ✅ 新标签页打开
   - ✅ 任何时刻只有一个标签页

---

## 9. 风险评估与缓解

### 9.1 潜在风险

| 风险 | 严重程度 | 概率 | 缓解措施 |
|------|---------|------|---------|
| 后台任务异常终止 | 低 | 中 | 异常捕获，静默退出 |
| Page 提前关闭冲突 | 低 | 高 | 检测 `page.is_closed()` |
| Cookie 文件未清理 | 低 | 低 | `finally` 块清理 |
| 资源泄漏 | 中 | 低 | 定期检查 Page 数量 |
| 性能下降 | 低 | 低 | 主流程速度不变 |

### 9.2 降级方案

如果人类行为模拟导致问题，可以快速降级：

```bash
# 方案 1：完全禁用
CDP_HUMAN_BEHAVIOR_ENABLED=false

# 方案 2：快速模式
CDP_QUICK_MODE=true

# 方案 3：缩短时间
CDP_WATCH_DURATION_MIN=5
CDP_WATCH_DURATION_MAX=10
CDP_PAGE_ALIVE_MIN=5
CDP_PAGE_ALIVE_MAX=10
```

---

## 10. 上线计划

### 10.1 开发阶段

- [x] 方案设计
- [ ] 代码实现
- [ ] 单元测试
- [ ] 集成测试

### 10.2 测试阶段

1. **本地测试**（1-2天）
   - 单任务流程验证
   - 并发场景验证
   - 资源占用监控
   - Chrome 行为观察

2. **灰度测试**（3-5天）
   - 部分任务开启人类行为（50%）
   - 监控成功率、403 错误率
   - 收集性能数据

3. **全量上线**（满足条件后）
   - 灰度测试成功率 > 95%
   - 403 错误率下降 > 50%
   - 无资源泄漏、异常

### 10.3 监控指标

| 指标 | 当前基线 | 目标 |
|------|---------|------|
| 下载成功率 | 80% | > 90% |
| 403 错误率 | 20% | < 10% |
| 主流程耗时（P95）| 8秒 | < 10秒 |
| Chrome 内存占用（峰值）| 500MB | < 500MB |
| 后台任务完成率 | - | > 95% |

---

## 11. 后续优化方向

### 11.1 短期优化（可选）

1. **更多人类行为**
   - 查看评论区（30% 概率）
   - 鼠标移动（轨迹模拟）
   - 点击暂停按钮（而非 JS 调用）

2. **行为模式库**
   - 预设多种观看模式（快速、中等、深度）
   - 根据视频时长调整行为

3. **账号维护模式**
   - 定期访问首页、订阅页
   - 偶尔点赞、评论

### 11.2 长期优化

1. **机器学习**
   - 收集真实人类行为数据
   - 训练行为生成模型
   - 自适应调整行为策略

2. **A/B 测试**
   - 对比不同行为策略的成功率
   - 自动选择最优策略

---

## 12. FAQ

### Q1: 主流程会变慢吗？

**A**: 不会。主流程在获取数据后立即返回（~5秒），人类行为在后台异步执行，不阻塞下载。

### Q2: 服务器资源够吗？

**A**: 足够。任何时刻只有 1 个 Page + 1 个后台任务，Chrome 内存增加约 100MB，完全可接受。

### Q3: 任务并发时会冲突吗？

**A**: 不会。新任务会关闭旧 Page（模拟人类关闭旧标签页），旧任务的后台行为自动检测并退出。

### Q4: 如何快速测试？

**A**: 设置 `CDP_QUICK_MODE=true` 跳过人类行为，主流程速度不变。

### Q5: 如果账号还是被封怎么办？

**A**: 可以进一步调整：
- 增加观看时长（40-60秒）
- 增加页面存活时长（60-120秒）
- 增加行为复杂度（查看评论、点赞等）
- 降低下载频率（增大任务间隔）

---

## 13. 模块拆分设计

### 13.1 为什么需要拆分

#### 13.1.1 当前问题

实施人类行为模拟方案后，`cdp_downloader.py` 将达到 **1900-2000 行**，存在以下问题：

| 问题 | 说明 | 影响 |
|------|------|------|
| **代码规模过大** | 单文件近 2000 行，违反"文件不超过 1000 行"的最佳实践 | 降低可读性和可维护性 |
| **职责过多** | 承担 8 个主要职责（Browser管理、Cookie、Headers、下载、行为模拟等） | 违反单一职责原则 |
| **测试困难** | 所有逻辑混在一起，难以编写针对性单元测试 | 测试覆盖率低，bug 难以定位 |
| **扩展困难** | 新增行为需要在巨大文件中定位代码 | 开发效率低 |

#### 13.1.2 职责分析

当前类承担的职责：

| 职责 | 代码行数 | 复杂度 |
|------|---------|--------|
| Browser 连接管理 | ~150 行 | 中 |
| Cookie 管理 | ~100 行 | 低 |
| Headers 提取 | ~80 行 | 低 |
| 音频 URL 提取 | ~100 行 | 中 |
| 音频下载（三层降级） | ~400 行 | 高 |
| 熔断器管理 | ~80 行 | 中 |
| 通知服务 | ~100 行 | 低 |
| **人类行为模拟（新增）** | **~300 行** | **中** |
| 总计 | **~1900 行** | - |

---

### 13.2 拆分方案

#### 13.2.1 目录结构

```
src/downloaders/cdp/
├── __init__.py                # 导出 CDPDownloader
├── downloader.py              # 主下载器（协调者，~700 行）
├── audio_downloader.py        # 音频下载逻辑（~400 行）
├── human_behavior.py          # 人类行为模拟（~300 行）✨ 新增
└── models.py                  # 已存在（AudioInfo 等）
```

#### 13.2.2 模块职责

**1. `downloader.py` - 主下载器（协调者）**

**职责**：
- 实现 `BaseDownloader` 接口
- 协调各个组件（Browser、Audio、HumanBehavior）
- 错误处理和熔断器管理
- 通知服务集成

**保留的方法**：
```python
# 接口实现
- name, downloader_type, is_available
- fetch_metadata()
- download_resources()  # 主流程协调
- health_check()
- should_retry()
- should_trigger_circuit_breaker()

# Browser 管理
- _get_browser()

# 熔断器
- _update_circuit_breaker()
- _handle_download_error()

# 通知
- _notify_connection_failure()
- _notify_circuit_breaker_open()
- _notify_cdp_recovered()

# POT Token
- _get_pot_token()
```

**代码量**：~700 行

---

**2. `audio_downloader.py` - 音频下载逻辑**

**职责**：
- yt-dlp 音频 URL 提取
- curl_cffi 下载（单线程 + 分片）
- yt-dlp 兜底下载
- 文件命名和路径管理

**方法**：
```python
class AudioDownloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract_audio_url(
        self,
        video_url: str,
        video_id: str,
        cookie_file: Path,
    ) -> AudioInfo:
        """使用 yt-dlp + cookies 提取音频 URL"""
        pass

    async def download_audio(
        self,
        audio_info: AudioInfo,
        video_id: str,
        task_id: str,
        output_dir: Path,
        headers: Dict[str, str],
    ) -> Path:
        """下载音频（三层降级）"""
        pass

    async def _download_with_curl_cffi(self, ...) -> bool:
        """curl_cffi 单线程下载"""
        pass

    async def _download_with_curl_cffi_multipart(self, ...) -> bool:
        """curl_cffi 分片下载"""
        pass

    async def _download_with_ytdlp(self, ...) -> Optional[Path]:
        """yt-dlp 兜底下载"""
        pass

    def _sanitize_filename(self, text: str) -> str:
        """清理文件名"""
        pass
```

**代码量**：~400 行

---

**3. `human_behavior.py` - 人类行为模拟 ✨ 新增**

**职责**：
- 快速数据获取（合并 Cookie + Headers 提取）
- 清理旧 Page（并发安全）
- 后台人类行为模拟
- 滚动、暂停、观看等行为

**方法**：
```python
class HumanBehaviorSimulator:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def cleanup_old_pages(self, context: BrowserContext) -> None:
        """清理旧 Page（模拟人类关闭旧标签页）"""
        pass

    async def quick_fetch_data(
        self,
        context: BrowserContext,
        video_url: str,
        video_id: str,
        task_id: str,
    ) -> Tuple[Page, Path, Dict[str, str]]:
        """快速获取 cookies + headers（2-3秒完成）"""
        pass

    async def background_human_behavior(
        self,
        page: Page,
        video_url: str,
        video_id: str,
        task_id: str,
    ) -> None:
        """后台异步执行：人类行为模拟（不阻塞主流程）"""
        pass

    async def _sleep_with_page_check(
        self,
        page: Page,
        duration: float,
        video_id: str,
    ) -> None:
        """分段睡眠，定期检查 Page 是否被关闭"""
        pass

    async def _simulate_scroll(self, page: Page) -> None:
        """模拟页面滚动"""
        pass

    async def _simulate_pause_resume(self, page: Page, duration: float) -> None:
        """模拟暂停/恢复视频"""
        pass

    def _cookies_to_netscape(self, cookies: list) -> str:
        """转换 cookies 为 Netscape 格式"""
        pass
```

**代码量**：~300 行

---

### 13.3 模块间调用关系

```
downloader.py (主协调者)
    ↓
    ├─> human_behavior.py
    │   ├─> cleanup_old_pages()          # 清理旧 Page
    │   ├─> quick_fetch_data()           # 获取 cookies + headers
    │   └─> background_human_behavior()  # 后台任务
    │
    └─> audio_downloader.py
        ├─> extract_audio_url()  # yt-dlp 提取 URL
        └─> download_audio()     # curl_cffi/ytdlp 下载
```

**调用示例**：

```python
# downloader.py
class CDPDownloader(BaseDownloader):
    def __init__(self, settings: Settings):
        self.settings = settings
        # 创建子模块实例
        self._audio_downloader = AudioDownloader(self.settings)
        self._behavior_simulator = HumanBehaviorSimulator(self.settings)

    async def download_resources(self, ...) -> DownloaderResult:
        # 1. 获取 Browser + Context
        browser, cdp_url = await self._get_browser()
        context = browser.contexts[0] if browser.contexts else await browser.new_context(...)

        try:
            # 2. 使用 HumanBehaviorSimulator
            await self._behavior_simulator.cleanup_old_pages(context)
            page, cookie_file, headers = await self._behavior_simulator.quick_fetch_data(
                context, video_url, video_id, task_id
            )

            # 3. 启动后台任务
            if self.settings.cdp_human_behavior_enabled:
                task = asyncio.create_task(
                    self._behavior_simulator.background_human_behavior(
                        page, video_url, video_id, task_id
                    )
                )
                # ...

            # 4. 使用 AudioDownloader
            audio_info = await self._audio_downloader.extract_audio_url(
                video_url, video_id, cookie_file
            )
            audio_path = await self._audio_downloader.download_audio(
                audio_info, video_id, task_id, output_dir, headers
            )

            # 5. 返回结果
            return DownloaderResult(...)
        finally:
            # ...
```

---

### 13.4 拆分效果对比

#### 13.4.1 代码规模

| 文件 | 拆分前 | 拆分后 |
|------|--------|--------|
| `cdp_downloader.py` | 1900 行 | - |
| `downloader.py` | - | 700 行 ✅ |
| `audio_downloader.py` | - | 400 行 ✅ |
| `human_behavior.py` | - | 300 行 ✅ |

**结果**：每个文件都控制在 **1000 行以内**。

#### 13.4.2 优势

| 优势 | 说明 |
|------|------|
| ✅ **单一职责** | 每个模块职责清晰，易于理解 |
| ✅ **可测试性** | 每个模块可以独立测试（如 `test_audio_downloader.py`） |
| ✅ **可维护性** | 修改音频下载逻辑不影响人类行为模拟 |
| ✅ **可复用性** | `AudioDownloader` 可能被其他下载器复用 |
| ✅ **易于扩展** | 新增行为（如查看评论）只需修改 `human_behavior.py` |

#### 13.4.3 劣势（可控）

| 劣势 | 解决方案 |
|------|---------|
| ⚠️ 文件数量增加 | 通过 `cdp/__init__.py` 统一导出，使用者无感知 |
| ⚠️ 需要管理导入 | 使用相对导入，保持简洁 |

---

### 13.5 实施步骤

#### 阶段 1：创建目录结构（5 分钟）

```bash
mkdir src/downloaders/cdp
touch src/downloaders/cdp/__init__.py
touch src/downloaders/cdp/downloader.py
touch src/downloaders/cdp/audio_downloader.py
touch src/downloaders/cdp/human_behavior.py
```

#### 阶段 2：移动代码（30 分钟）

1. **复制原文件**（保留备份）：
   ```bash
   cp src/downloaders/cdp_downloader.py src/downloaders/cdp_downloader.py.bak
   ```

2. **提取 AudioDownloader**：
   - 将音频下载相关方法移动到 `audio_downloader.py`
   - 测试：运行现有测试，确保无破坏

3. **提取 HumanBehaviorSimulator**：
   - 实现设计方案中的新方法
   - 修改原 `_export_cookies()` 和 `_extract_request_headers()` 调用

4. **保留主协调者**：
   - `downloader.py` 保留协调逻辑
   - 创建子模块实例

#### 阶段 3：更新导入（5 分钟）

```python
# src/downloaders/cdp/__init__.py
from .downloader import CDPDownloader

__all__ = ["CDPDownloader"]
```

```python
# 其他文件（如 manager.py）更新导入
# 从
from src.downloaders.cdp_downloader import CDPDownloader

# 改为
from src.downloaders.cdp import CDPDownloader
```

#### 阶段 4：测试验证（10 分钟）

```bash
# 1. 运行单元测试
pytest tests/test_cdp_downloader.py -v

# 2. 运行集成测试
pytest tests/integration/test_cdp_integration.py -v

# 3. 手动测试
python -m src.main
# 提交下载任务，观察日志
```

---

### 13.6 状态共享处理

**问题**：类级别共享状态如何处理？

```python
# 当前代码中的共享状态
CDPDownloader._browser: Optional[Browser]
CDPDownloader._browser_lock: asyncio.Lock
CDPDownloader._circuit_breaker_state: str
CDPDownloader._notification_cache: Dict[str, float]
```

**解决方案**：

1. **保留在主类中**：共享状态仍然由 `CDPDownloader` 管理
2. **通过依赖注入传递**：子模块需要时，通过参数传递

```python
# audio_downloader.py 不需要访问共享状态
audio_downloader = AudioDownloader(self.settings)

# human_behavior.py 也不需要访问共享状态
behavior_simulator = HumanBehaviorSimulator(self.settings)

# 调用示例
page, cookie_file, headers = await behavior_simulator.quick_fetch_data(
    context, video_url, video_id, task_id
)
```

---

### 13.7 注意事项

1. **保留备份**：拆分前务必备份原文件
2. **增量测试**：每移动一个模块，立即测试
3. **更新文档**：更新 README.md 和设计文档中的文件路径
4. **更新 .gitignore**：确保 `*.bak` 文件不被提交

---

## 14. 附录

### 14.1 相关文件清单

| 文件 | 说明 |
|------|------|
| `src/config.py` | 新增配置项 |
| `src/downloaders/cdp/downloader.py` | 主下载器（拆分后） |
| `src/downloaders/cdp/audio_downloader.py` | 音频下载模块（拆分后） |
| `src/downloaders/cdp/human_behavior.py` | 人类行为模拟模块（拆分后） |
| `.env.example` | 配置示例 |
| `README.md` | 更新配置说明 |
| `docs/cdp_human_behavior_design.md` | 本文档 |

### 14.2 参考资料

- [Playwright 文档](https://playwright.dev/python/)
- [YouTube 风控机制分析](https://github.com/yt-dlp/yt-dlp/wiki/FAQ)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)

---

## 15. 实施检查清单

### 15.1 代码修改检查清单（必须完成）

开发者在实施此方案前，必须完成以下修改：

#### ✅ 修改 1：src/config.py - 添加人类行为模拟配置

```python
# ========== CDP 人类行为模拟配置 ==========
cdp_human_behavior_enabled: bool = Field(
    default=True,
    description="启用 CDP 人类行为模拟（降低风控）"
)

cdp_quick_mode: bool = Field(
    default=False,
    description="快速模式：跳过人类行为模拟（用于测试）"
)

cdp_watch_duration_min: int = Field(
    default=20,
    ge=5,
    le=120,
    description="视频观看最小时长（秒）"
)
cdp_watch_duration_max: int = Field(
    default=40,
    ge=10,
    le=180,
    description="视频观看最大时长（秒）"
)

cdp_page_alive_min: int = Field(
    default=30,
    ge=10,
    le=300,
    description="页面关闭前最小存活时长（秒）"
)
cdp_page_alive_max: int = Field(
    default=60,
    ge=20,
    le=600,
    description="页面关闭前最大存活时长（秒）"
)

cdp_scroll_probability: float = Field(
    default=0.8,
    ge=0.0,
    le=1.0,
    description="滚动页面的概率"
)
cdp_pause_probability: float = Field(
    default=0.2,
    ge=0.0,
    le=1.0,
    description="暂停/恢复视频的概率"
)
```

#### ✅ 修改 2：src/downloaders/cdp_downloader.py - 核心逻辑改造

**2.1 __init__ 方法 - 添加并发警告**

```python
def __init__(self, settings: Settings):
    # ... 现有代码 ...

    # 警告：人类行为模拟要求单并发
    if (
        self.settings.cdp_human_behavior_enabled
        and self.settings.download_concurrency > 1
    ):
        logger.warning(
            "[cdp] CDP human behavior simulation requires DOWNLOAD_CONCURRENCY=1. "
            "Concurrent tasks may interfere with each other, causing background "
            "behaviors to terminate early. Please set DOWNLOAD_CONCURRENCY=1 "
            "or disable human behavior (CDP_HUMAN_BEHAVIOR_ENABLED=false)."
        )
```

**2.2 download_resources 方法 - Cookie 清理逻辑修复**

在方法开头添加标志：
```python
background_task_started = False  # ← 新增
```

在启动后台任务时设置标志：
```python
if self.settings.cdp_human_behavior_enabled and not self.settings.cdp_quick_mode:
    task = asyncio.create_task(
        self._background_human_behavior(page, video_url, video_id, task_id)
    )

    # ← 新增：添加异常处理回调
    def handle_task_exception(t):
        try:
            t.result()
        except Exception as e:
            logger.error(
                f"[cdp] Background behavior task failed for {video_id}: {e}",
                exc_info=True
            )

    task.add_done_callback(handle_task_exception)
    background_task_started = True  # ← 新增
```

在 finally 块中修改 Cookie 清理逻辑：
```python
finally:
    # 不关闭 Context（保持复用）

    # ← 修改：仅在后台任务未启动时清理 Cookie
    if not background_task_started:
        cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
        if cookie_file.exists():
            cookie_file.unlink()
            logger.debug(f"[cdp] Cleaned up cookie file: {cookie_file}")
```

**2.3 新增方法实现**

- `_cleanup_old_pages()` - 清理旧 Page（见 4.2.2 节）
- `_quick_fetch_data()` - 快速获取数据（见 4.2.3 节）
- `_background_human_behavior()` - 后台人类行为模拟（见 4.2.4 节）
- `_sleep_with_page_check()` - 分段睡眠检查（见 4.2.5 节）
- `_simulate_scroll()` - 模拟滚动（见 4.2.6 节）
- `_simulate_pause_resume()` - 模拟暂停恢复（见 4.2.7 节）

**2.4 移除方法**

- `_export_cookies()` - 合并到 `_quick_fetch_data()`
- `_extract_request_headers()` - 合并到 `_quick_fetch_data()`

#### ✅ 修改 3：src/main.py - 添加临时文件清理

在 `lifespan()` 函数的启动部分添加：

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ... 现有启动代码 ...

    # 清理过期的临时文件（启动时执行）
    await _cleanup_stale_temp_files(settings)

    # ... 其他启动代码 ...
```

在文件末尾添加清理函数：

```python
async def _cleanup_stale_temp_files(settings: Settings) -> None:
    """清理过期的 CDP 临时文件"""
    tmp_dir = settings.data_dir / "tmp"

    if not tmp_dir.exists():
        return

    now = time.time()
    cleaned_count = 0

    # 清理超过 1 小时的 CDP cookie 文件
    for cookie_file in tmp_dir.glob("cdp_*.cookies.txt"):
        try:
            # 清理超过 1 小时的文件
            if now - cookie_file.stat().st_mtime > 3600:
                cookie_file.unlink()
                cleaned_count += 1
        except Exception as e:
            logger.warning(f"[cleanup] Failed to remove {cookie_file}: {e}")

    if cleaned_count > 0:
        logger.info(f"[cleanup] Removed {cleaned_count} stale CDP cookie files")
```

#### ✅ 修改 4：.env.example - 添加配置说明

在 CDP 配置章节添加：

```bash
# ========== CDP 人类行为模拟配置 ==========
# 模拟真实人类浏览行为，降低 YouTube 风控概率

# 启用人类行为模拟（推荐）
CDP_HUMAN_BEHAVIOR_ENABLED=true

# 快速模式：跳过人类行为（仅用于测试）
CDP_QUICK_MODE=false

# 视频观看时长（秒，随机范围）
CDP_WATCH_DURATION_MIN=20
CDP_WATCH_DURATION_MAX=40

# 页面存活时长（秒，随机范围）
CDP_PAGE_ALIVE_MIN=30
CDP_PAGE_ALIVE_MAX=60

# 行为概率（0.0 - 1.0）
CDP_SCROLL_PROBABILITY=0.8   # 80% 概率滚动页面
CDP_PAUSE_PROBABILITY=0.2    # 20% 概率暂停/恢复视频

# 重要：启用人类行为模拟时，必须设置 DOWNLOAD_CONCURRENCY=1
# 否则并发任务会相互干扰，导致后台行为提前终止
DOWNLOAD_CONCURRENCY=1
```

#### ✅ 修改 5：README.md - 更新文档

在 CDP 配置章节添加人类行为模拟说明（参考 6.1-6.3 节的配置示例）。

---

### 15.2 实施流程

1. **阶段 1：代码实现**（1-2 天）
   - [ ] 完成 src/config.py 修改
   - [ ] 完成 src/downloaders/cdp/ 模块拆分
   - [ ] 完成 src/main.py 修改
   - [ ] 更新 .env.example
   - [ ] 更新 README.md

2. **阶段 2：本地测试**（1-2 天）
   - [ ] 单元测试：测试新增方法（8.1 节）
   - [ ] 集成测试：测试完整流程（8.2 节）
   - [ ] 手动测试：观察 Chrome 行为（8.3 节）
   - [ ] 验证：Cookie 文件正确清理
   - [ ] 验证：后台任务异常捕获正常

3. **阶段 3：灰度测试**（3-5 天）
   - [ ] 快速模式验证：CDP_QUICK_MODE=true
   - [ ] 真实模式灰度：50% 任务启用
   - [ ] 监控指标：403 错误率、下载成功率
   - [ ] 收集数据：对比启用前后效果

4. **阶段 4：全量上线或回滚**
   - [ ] 如果 403 错误率下降 > 20%：全量上线
   - [ ] 否则：调整参数或回滚

---

### 15.3 验证清单

实施完成后，使用以下清单验证：

#### 功能验证

- [ ] **主流程速度不受影响**：下载任务 < 10 秒返回
- [ ] **后台任务正常执行**：Chrome 页面保持 30-60 秒
- [ ] **Cookie 文件正确清理**：检查 `data/tmp/` 目录无残留
- [ ] **异常捕获正常**：触发异常后日志中有 `exc_info=True` 的记录
- [ ] **并发警告正常**：`DOWNLOAD_CONCURRENCY=2` 时有警告日志

#### 并发安全验证

- [ ] **单任务场景**：Page 保持 50-100 秒，正常关闭
- [ ] **任务顺序到达**：新任务关闭旧 Page，旧任务静默退出
- [ ] **任务快速连续**：旧任务提前终止，无异常

#### 资源占用验证

- [ ] **Chrome 内存**：任何时刻最多 1 个 Page（< 500MB）
- [ ] **后台任务数量**：任何时刻最多 1 个
- [ ] **临时文件清理**：服务重启后自动清理过期文件

---

### 15.4 已知限制

实施前请明确以下限制：

1. **必须单并发**：`DOWNLOAD_CONCURRENCY` 必须为 1，否则并发任务会相互干扰
2. **依赖外部 Chrome**：需要启动外部 Chrome 并开启 CDP
3. **风控效果待验证**：需通过 A/B 测试验证实际效果
4. **资源占用增加**：每个任务的 Chrome 资源占用时长增加 50-100 秒

---

**文档结束**
