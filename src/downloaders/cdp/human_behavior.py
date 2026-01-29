"""
CDP 人类行为模拟模块。

负责模拟真实人类浏览行为，降低 YouTube 风控概率。

职责：
- 快速数据获取（合并 Cookie + Headers 提取）
- 清理旧 Page（并发安全）
- 后台人类行为模拟
- 滚动、暂停、观看等行为
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from playwright.async_api import BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    BrowserContext = None  # type: ignore
    Page = None  # type: ignore

from src.config import Settings
from src.utils.logger import logger


class HumanBehaviorSimulator:
    """
    人类行为模拟器。

    模拟真实人类浏览行为：
    - 页面滚动
    - 视频观看
    - 暂停/恢复
    - 随机停留时间
    """

    def __init__(self, settings: Settings):
        """
        初始化人类行为模拟器。

        Args:
            settings: 应用配置
        """
        self.settings = settings

    async def cleanup_old_pages(
        self,
        context: BrowserContext,
        keep_last: bool = True
    ) -> Optional[Page]:
        """
        清理旧的 Page（模拟人类关闭旧标签页）。

        并发安全：
        - 新任务开始时，关闭所有旧的 Page
        - 旧任务的后台任务会自动检测 Page 关闭并退出
        - 确保任何时刻只有一个视频在播放

        人类行为模拟：
        - 就像人类关闭旧标签页再打开新标签页
        - 不会同时播放多个视频

        Chrome 存活保证：
        - 保留最后一个 Page，避免关闭所有标签页导致 Chrome 退出
        - 调用方需要在创建新 Page 后手动关闭保留的 Page

        Args:
            context: BrowserContext 实例
            keep_last: 是否保留最后一个 Page（避免 Chrome 退出）

        Returns:
            保留的 Page（需要在创建新 Page 后手动关闭），如果没有保留则返回 None
        """
        if not context.pages:
            logger.debug("[cdp] No old pages to clean up")
            return None

        old_pages = list(context.pages)

        # 如果只有 1 个 Page 且需要保留，不关闭
        if len(old_pages) == 1 and keep_last:
            logger.debug("[cdp] Only 1 page, keeping it alive")
            return old_pages[0]

        # 决定要关闭的 Page
        if keep_last and len(old_pages) > 1:
            pages_to_close = old_pages[:-1]
            kept_page = old_pages[-1]
            logger.info(
                f"[cdp] Cleaning up {len(pages_to_close)} old page(s), "
                f"keeping 1 alive (simulating closing old tabs)"
            )
        else:
            pages_to_close = old_pages
            kept_page = None
            logger.info(
                f"[cdp] Cleaning up {len(old_pages)} old page(s) "
                "(simulating closing old tabs)"
            )

        # 关闭旧 Page
        for i, page in enumerate(pages_to_close):
            try:
                page_url = page.url if not page.is_closed() else "unknown"
                await page.close()
                logger.debug(f"[cdp] Closed old page {i+1}/{len(pages_to_close)}: {page_url}")
            except Exception as e:
                logger.debug(f"[cdp] Failed to close old page {i+1}: {e}")

        return kept_page

    async def quick_fetch_data(
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

        Args:
            context: BrowserContext 实例
            video_url: 视频 URL
            video_id: 视频 ID
            task_id: 任务 ID（用于临时文件命名）

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

            # 触发视频播放（捕获 headers）
            try:
                # 等待视频元素加载（关键：必须等待 <video> 而不是 #movie_player）
                await page.wait_for_selector("video", timeout=15000)

                # 触发视频播放
                await page.evaluate("""() => {
                    const video = document.querySelector('video');
                    if (!video) return;
                    video.muted = true;
                    const p = video.play();
                    if (p && p.catch) p.catch(() => {});
                }""")

                # 等待播放触发请求（关键：让浏览器有时间刷新 cookies/session）
                await asyncio.sleep(2)

                # 等待捕获 headers（最多 10 秒，与重构前一致）
                await asyncio.wait_for(headers_captured.wait(), timeout=10)
                logger.debug(f"[cdp] Captured {len(captured_headers)} headers")
            except asyncio.TimeoutError:
                logger.warning("[cdp] Failed to capture headers, using defaults")
            except Exception as e:
                logger.debug(f"[cdp] Could not trigger playback for headers extraction: {e}")

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

    async def background_human_behavior(
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
            # 注意：如果是最后一个 Page，保留它以避免 Chrome 退出
            try:
                if not page.is_closed():
                    # 检查是否是最后一个 Page
                    context = page.context
                    if len(context.pages) > 1:
                        await page.close()
                        logger.debug(f"[cdp] Closed page for {video_id}")
                    else:
                        logger.debug(
                            f"[cdp] Keeping last page alive for {video_id} "
                            "(avoid Chrome exit)"
                        )
            except Exception as e:
                logger.debug(f"[cdp] Failed to close page (already closed?): {e}")

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

    def _cookies_to_netscape(self, cookies: list) -> str:
        """
        转换 cookies 为 Netscape 格式。

        仅保留 YouTube 和 Google 相关的 cookies。
        """
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain = c.get("domain", "")
            if "youtube.com" not in domain and "google.com" not in domain:
                continue

            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", 0))
            if expires < 0:
                expires = 0
            name = c.get("name", "")
            value = c.get("value", "")

            lines.append("\t".join([domain, flag, path, secure, str(expires), name, value]))

        return "\n".join(lines)
