"""
CDP (Chrome DevTools Protocol) 主下载器。

基于真实浏览器的 YouTube 音频下载器，通过以下技术降低 403 风控：
- 真实浏览器 Cookies（每次刷新）
- CDP 捕获的真实 Headers
- curl_cffi TLS 指纹模拟
- 可选 poToken 支持
- 人类行为模拟（后台异步执行）

特性：
- 多客户端并发隔离（共享 Browser + 独立 Context）
- 多 CDP 实例故障转移
- 三层降级下载（curl_cffi 分片 → curl_cffi → yt-dlp）
- 熔断器保护
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from cachetools import TTLCache

try:
    from playwright.async_api import Browser, BrowserContext, async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None  # type: ignore
    BrowserContext = None  # type: ignore

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.base import BaseDownloader
from src.downloaders.cdp.audio_downloader import AudioDownloader
from src.downloaders.cdp.human_behavior import HumanBehaviorSimulator
from src.downloaders.cdp.models import (
    AudioInfo,
    CDPHealthStatus,
    CDPInstanceHealth,
)
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, DownloaderType, VideoMetadata
from src.utils.logger import logger

# 延迟导入通知服务（避免循环依赖）
try:
    from src.services.notify import NotificationService
    NOTIFICATION_AVAILABLE = True
except ImportError:
    NOTIFICATION_AVAILABLE = False
    NotificationService = None  # type: ignore[assignment, misc]


class CDPDownloader(BaseDownloader):
    """
    基于 Chrome DevTools Protocol 的 YouTube 音频下载器。

    工作流程：
    1. 连接到外部 Chrome（共享 Browser）
    2. 创建/复用 BrowserContext（每任务独立）
    3. 清理旧 Page（保留最后一个，避免 Chrome 退出）
    4. 快速获取 cookies + headers（创建新 Page）
    5. 关闭保留的旧 Page（此时新 Page 已创建）
    6. 启动后台人类行为模拟（异步，不阻塞）
    7. 使用 yt-dlp + cookies 提取音频 URL
    8. 下载音频（三层降级）
    9. 主流程立即返回结果
    10. 后台任务继续模拟人类行为（30-60秒）
    11. 后台任务清理临时文件和关闭 Page

    并发安全性：
    - 共享 Browser 实例（所有任务共享同一个 CDP 连接）
    - 复用 BrowserContext（避免 Chrome 不断打开新窗口）
    - 单 Page 策略（任何时刻只有一个视频在播放）
    - 通过 asyncio.Lock 保证 Browser 连接的线程安全
    """

    # ========== 类级别共享状态 ==========
    _playwright: Optional[object] = None  # Playwright client (node driver)
    _browser: Optional[Browser] = None
    _browser_lock: Optional[asyncio.Lock] = None  # 延迟初始化
    _last_health_check: float = 0
    _cdp_health_status: Dict[str, CDPInstanceHealth] = {}

    # 熔断器状态（全局）
    _circuit_breaker_state: str = "CLOSED"  # CLOSED/OPEN/HALF_OPEN
    _circuit_open_until: float = 0
    _health_check_failures: int = 0

    # 通知服务缓存：使用 TTLCache 防止 key 无界累积
    # maxsize=512 上限保护，ttl=600s 兜底（业务侧仍按 cdp_notify_cooldown 判定节流）
    _notification_cache: TTLCache = TTLCache(maxsize=512, ttl=600)

    def __init__(self, settings: Settings):
        """
        初始化 CDP 下载器。

        Args:
            settings: 应用配置
        """
        self.settings = settings
        self._notify_service: Optional[NotificationService] = None

        # 后台 fire-and-forget 任务的强引用集合
        # asyncio 的已知坑：仅持有 task 局部变量会被 GC 提前回收，任务中断
        # 参考 https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
        self._background_tasks: set[asyncio.Task] = set()

        # 创建子模块实例
        self._audio_downloader = AudioDownloader(self.settings, self.name)
        self._behavior_simulator = HumanBehaviorSimulator(self.settings)

        # 延迟初始化锁（避免事件循环问题）
        if CDPDownloader._browser_lock is None:
            CDPDownloader._browser_lock = asyncio.Lock()

        # 初始化 CDP 实例健康状态
        for cdp_url in self.settings.cdp_url_list:
            if cdp_url not in CDPDownloader._cdp_health_status:
                CDPDownloader._cdp_health_status[cdp_url] = CDPInstanceHealth(
                    cdp_url=cdp_url,
                    is_healthy=True,
                    last_check_time=0,
                    consecutive_failures=0,
                    circuit_state="CLOSED",
                    circuit_open_until=0,
                )

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

    def _track_background_task(self, task: asyncio.Task) -> None:
        """注册一个 fire-and-forget asyncio.Task。

        加入 self._background_tasks 提供强引用，完成后自动 discard，
        防止任务被 GC 提前回收。

        Args:
            task: 已通过 asyncio.create_task 创建的任务
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        """
        关闭 CDP 下载器，释放所有资源。

        清理顺序：
        1. 取消所有未完成的后台任务
        2. 关闭 HumanBehaviorSimulator 持有的 owned pages
        3. 关闭共享 Browser 实例
        4. 重置类级别状态
        """
        # 取消所有未完成的后台任务（fire-and-forget）
        pending = [t for t in list(self._background_tasks) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.debug(f"[cdp] Cancelled {len(pending)} background task(s) on close")

        # 关闭 behavior simulator 持有的 owned pages
        if self._behavior_simulator:
            for page in list(self._behavior_simulator._owned_pages):
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass
            self._behavior_simulator._owned_pages.clear()

        # 关闭共享 Browser 实例
        if CDPDownloader._browser is not None:
            try:
                await CDPDownloader._browser.close()
                logger.info("[cdp] Browser closed")
            except Exception as e:
                logger.debug(f"[cdp] Error closing browser: {e}")
            finally:
                CDPDownloader._browser = None

        # 停止 Playwright client (释放 node driver 进程)
        if CDPDownloader._playwright is not None:
            try:
                await CDPDownloader._playwright.stop()
                logger.info("[cdp] Playwright driver stopped")
            except Exception as e:
                logger.debug(f"[cdp] Error stopping playwright: {e}")
            finally:
                CDPDownloader._playwright = None

    # ========== BaseDownloader 接口实现 ==========

    @property
    def name(self) -> str:
        """下载器名称。"""
        return "cdp"

    @property
    def downloader_type(self) -> DownloaderType:
        """下载器类型。"""
        return DownloaderType.CDP

    @property
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        检查项：
        1. CDP_ENABLED 配置
        2. Playwright 库是否可用
        3. 熔断器是否开启
        """
        if not self.settings.cdp_enabled:
            return False

        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("[cdp] Playwright not installed")
            return False

        # 检查熔断器
        if CDPDownloader._circuit_breaker_state == "OPEN":
            if time.time() < CDPDownloader._circuit_open_until:
                return False
            else:
                # 进入半开状态
                CDPDownloader._circuit_breaker_state = "HALF_OPEN"
                logger.info("[cdp] Circuit breaker entering HALF_OPEN state")

        return True

    async def health_check(self) -> CDPHealthStatus:
        """
        健康检查：测试 CDP 连接。

        检查项：
        1. CDP 服务是否可达
        2. 能否成功创建 Browser 连接
        3. 更新熔断器状态

        Returns:
            CDPHealthStatus: 健康状态信息

        副作用：
            - 更新熔断器状态
            - 连接失败时发送企微通知（带频率限制）
        """
        now = time.time()

        # 避免频繁健康检查
        if now - CDPDownloader._last_health_check < self.settings.cdp_health_check_interval:
            return CDPHealthStatus(
                is_healthy=CDPDownloader._circuit_breaker_state != "OPEN",
                last_check_time=CDPDownloader._last_health_check,
                consecutive_failures=CDPDownloader._health_check_failures,
                circuit_state=CDPDownloader._circuit_breaker_state,
                circuit_open_until=CDPDownloader._circuit_open_until,
            )

        CDPDownloader._last_health_check = now

        try:
            # 尝试获取 Browser 连接
            browser, cdp_url = await self._get_browser()

            # 简单验证：检查是否可以创建 Context
            context = await browser.new_context()
            await context.close()

            # 健康检查成功
            await self._update_circuit_breaker(success=True)

            logger.info(
                "[cdp] Health check passed",
                extra={
                    "event": "cdp_health_check",
                    "success": True,
                    "circuit_state": CDPDownloader._circuit_breaker_state,
                    "cdp_url": cdp_url,
                },
            )

            return CDPHealthStatus(
                is_healthy=True,
                last_check_time=now,
                consecutive_failures=0,
                circuit_state=CDPDownloader._circuit_breaker_state,
                circuit_open_until=0,
            )

        except Exception as e:
            error_msg = str(e)

            # 健康检查失败
            await self._update_circuit_breaker(success=False)

            # 发送通知（带频率限制）
            await self._notify_connection_failure(error_msg, "health_check")

            logger.warning(
                "[cdp] Health check failed",
                extra={
                    "event": "cdp_health_check",
                    "success": False,
                    "error": error_msg[:200],
                    "consecutive_failures": CDPDownloader._health_check_failures,
                    "circuit_state": CDPDownloader._circuit_breaker_state,
                },
            )

            return CDPHealthStatus(
                is_healthy=False,
                last_check_time=now,
                consecutive_failures=CDPDownloader._health_check_failures,
                circuit_state=CDPDownloader._circuit_breaker_state,
                circuit_open_until=CDPDownloader._circuit_open_until,
            )

    async def fetch_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[dict]:
        """
        获取视频元数据。

        CDP 不负责元数据获取，返回 None。
        """
        return None

    async def download_resources(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        下载资源（音频和/或字幕）。

        CDP 下载器支持三种模式：
        1. 仅音频：下载音频文件
        2. 仅字幕：下载字幕文件
        3. 音频+字幕：在一次页面访问中同时处理

        核心优化：一次 CDP 页面访问 + 一次 yt-dlp 调用获取所有信息。

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult

        Raises:
            DownloaderError: 下载失败
        """
        logger.info(
            f"[cdp] Starting download for {video_id}: "
            f"audio={include_audio}, transcript={include_transcript}"
        )

        # 生成任务 ID（用于临时文件命名）
        task_id = f"cdp_{video_id}_{int(time.time())}"
        background_task_started = False  # 标志后台任务是否启动

        try:
            # 1. 获取共享的 Browser（多实例故障转移）
            browser, cdp_url = await self._get_browser()
            logger.debug(f"[cdp] Connected to browser at {cdp_url}")

            # 2. 获取或创建 BrowserContext（优先复用已存在的 context）
            context = await self._get_or_create_context(browser)
            context.set_default_timeout(30000)

            try:
                # 3. 清理旧 Page（仅在启用人类行为模拟时）
                kept_page = None
                if self.settings.cdp_human_behavior_enabled and not self.settings.cdp_quick_mode:
                    kept_page = await self._behavior_simulator.cleanup_old_pages(
                        context, keep_last=True
                    )

                # 4. 快速获取数据（创建新 Page，获取 cookie + headers）
                page, cookie_file, headers, video_duration = await self._behavior_simulator.quick_fetch_data(
                    context, video_url, video_id, task_id
                )

                # 4.5. 同步 Cookie 到共享位置（让 ytdlp 也能使用）
                await self._sync_cookie_to_shared_location(cookie_file)

                # 5. 关闭保留的旧 Page（加超时保护）
                if kept_page and not kept_page.is_closed():
                    try:
                        await asyncio.wait_for(kept_page.close(), timeout=5)
                        logger.debug("[cdp] Closed kept page after new page created")
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning(f"[cdp] Failed to close kept page: {e}")
                    self._behavior_simulator._owned_pages.discard(kept_page)

                # 6. 启动后台人类行为模拟（异步，不阻塞）
                background_task_started = await self._start_background_behavior(
                    page, video_url, video_id, task_id, video_duration
                )

                # 7. 获取 PO Token（如果启用）
                pot_token = await self._get_pot_token_safe(video_id)

                # 8. 一次性提取视频信息（音频 URL + 字幕信息）
                extracted_info = await self._audio_downloader.extract_video_info(
                    video_url=video_url,
                    video_id=video_id,
                    cookie_file=cookie_file,
                    task_id=task_id,
                    pot_token=pot_token,
                    include_audio=include_audio,
                    include_transcript=include_transcript,
                )

                # 9. 下载音频（如果需要）
                audio_path = None
                if include_audio:
                    if extracted_info.audio_info:
                        audio_path = await self._audio_downloader.download_audio(
                            audio_info=extracted_info.audio_info,
                            video_id=video_id,
                            task_id=task_id,
                            output_dir=output_dir,
                            headers=headers,
                        )
                        logger.info(
                            f"[cdp] Audio downloaded: {audio_path.name} "
                            f"({audio_path.stat().st_size} bytes)"
                        )
                    else:
                        raise DownloaderError(
                            message="Failed to extract audio URL",
                            error_code=ErrorCode.CDP_NO_AUDIO_URL,
                            downloader=self.name,
                        )

                # 10. 下载字幕（如果需要）
                transcript_path = None
                has_transcript = False
                if include_transcript:
                    if extracted_info.subtitles:
                        # 选择最佳字幕（已按优先级排序）
                        best_subtitle = extracted_info.subtitles[0]
                        transcript_path = await self._audio_downloader.download_subtitle(
                            video_url=video_url,
                            video_id=video_id,
                            cookie_file=cookie_file,
                            output_dir=output_dir,
                            subtitle_lang=best_subtitle.lang,
                        )
                        if transcript_path:
                            has_transcript = True
                            logger.info(
                                f"[cdp] Subtitle downloaded: {transcript_path.name} "
                                f"(lang={best_subtitle.lang}, auto={best_subtitle.is_auto})"
                            )
                        else:
                            logger.warning(f"[cdp] Subtitle download failed for {video_id}")
                    else:
                        logger.info(f"[cdp] No subtitles available for {video_id}")

                # 11. 构造成功结果
                metadata = VideoMetadata(
                    video_id=video_id,
                    title=extracted_info.title,
                    source_downloader=self.name,
                )

                result = DownloaderResult(
                    success=True,
                    downloader=self.name,
                    video_metadata=metadata,
                    audio_path=audio_path,
                    transcript_path=transcript_path,
                    has_transcript=has_transcript,
                )

                # 记录成功日志
                success_parts = []
                if audio_path:
                    success_parts.append(f"audio={audio_path.stat().st_size}B")
                if transcript_path:
                    success_parts.append(f"transcript={transcript_path.name}")
                logger.info(f"[cdp] Successfully downloaded {video_id}: {', '.join(success_parts)}")

                # 更新熔断器（成功）
                await self._update_circuit_breaker(success=True)

                return result

            finally:
                # 不关闭 Context（保持复用）
                # 仅在后台任务未启动时清理 Cookie
                if not background_task_started:
                    cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
                    if cookie_file.exists():
                        cookie_file.unlink()
                        logger.debug(f"[cdp] Cleaned up cookie file: {cookie_file}")

        except Exception as e:
            # 检查是否是浏览器连接已关闭的错误
            error_msg = str(e).lower()
            if self._is_browser_connection_lost(error_msg):
                # 浏览器连接已断开，清空引用以便下次重连
                assert CDPDownloader._browser_lock is not None
                async with CDPDownloader._browser_lock:
                    if CDPDownloader._browser is not None:
                        logger.warning(
                            f"[cdp] Browser connection lost ({self._match_connection_error(error_msg)}), "
                            "clearing browser reference for reconnection"
                        )
                        CDPDownloader._browser = None

            # 更新熔断器（失败）
            await self._update_circuit_breaker(success=False)

            # 处理错误
            await self._handle_download_error(e, video_id, task_id)
            raise

    @staticmethod
    def _is_browser_connection_lost(error_msg: str) -> bool:
        """判断错误是否表示浏览器连接已断开（需要清空引用以触发重连）。"""
        # "target closed" - Playwright CDP 连接目标关闭
        if "target" in error_msg and "closed" in error_msg:
            return True
        # "the handler is closed" - 底层传输通道关闭（WriteUnixTransport closed）
        if "handler" in error_msg and "closed" in error_msg:
            return True
        # "connection closed" - 通用连接关闭
        if "connection" in error_msg and "closed" in error_msg:
            return True
        return False

    @staticmethod
    def _match_connection_error(error_msg: str) -> str:
        """返回匹配到的连接错误类型（用于日志）。"""
        if "target" in error_msg and "closed" in error_msg:
            return "target closed"
        if "handler" in error_msg and "closed" in error_msg:
            return "handler closed"
        if "connection" in error_msg and "closed" in error_msg:
            return "connection closed"
        return "unknown connection error"

    def should_retry(self, error: Exception) -> bool:
        """
        判断错误是否应该重试当前下载器。

        CDP 下载器的重试策略：
        - 连接超时 → 重试
        - 网络错误 → 重试
        - 403 错误 → 不重试（降级）
        - 其他错误 → 不重试（降级）
        """
        if isinstance(error, DownloaderError):
            # 超时错误可以重试
            if error.error_code in [
                ErrorCode.CDP_CONNECTION_TIMEOUT,
                ErrorCode.CDP_PAGE_TIMEOUT,
            ]:
                return True
        return False

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """
        判断错误是否应该触发熔断器。

        触发熔断的错误：
        - 连接失败
        - 连接超时
        - Chrome 未就绪
        - nsig/n challenge 失败（全局性问题，yt-dlp 版本过旧）

        不触发熔断的错误：
        - 视频特定错误（如无音频 URL）
        - Cookie 导出失败（可能未登录）
        """
        if isinstance(error, DownloaderError):
            if error.error_code in [
                ErrorCode.CDP_CONNECTION_FAILED,
                ErrorCode.CDP_CONNECTION_TIMEOUT,
                ErrorCode.CDP_CHROME_NOT_READY,
                ErrorCode.CDP_NSIG_FAILED,  # 全局性问题，需要更新 yt-dlp
            ]:
                return True
        return False

    # ========== 内部方法：Browser 管理 ==========

    async def _get_browser(self) -> Tuple[Browser, str]:
        """
        获取或创建浏览器连接（支持多实例故障转移）。

        实现：
        - 共享 Browser 实例（所有任务共享同一个连接）
        - 通过 asyncio.Lock 保证线程安全
        - 支持多 CDP 实例（从 CDP_URLS 配置）
        - 故障转移策略：sequential（顺序）或 random（随机）

        Returns:
            (Browser, cdp_url): 浏览器实例和实际使用的 CDP URL

        Raises:
            DownloaderError: 所有实例都连接失败
        """
        assert CDPDownloader._browser_lock is not None
        async with CDPDownloader._browser_lock:
            # 如果已有连接，先验证连接是否真的活着
            if CDPDownloader._browser is not None:
                try:
                    # 检查浏览器是否真的连接着（而非仅检查引用存在）
                    if CDPDownloader._browser.is_connected():
                        # 返回第一个健康的 URL（简化版）
                        for url in self.settings.cdp_url_list:
                            if self._cdp_health_status[url].is_healthy:
                                return CDPDownloader._browser, url
                    else:
                        # 连接已断开，清空引用
                        logger.warning(
                            "[cdp] Existing browser is not connected (disconnected), "
                            "will reconnect"
                        )
                        CDPDownloader._browser = None
                except Exception as e:
                    # 检查连接状态时出错，可能浏览器已关闭
                    logger.warning(
                        f"[cdp] Failed to check browser connection status: {e}, "
                        "will reconnect"
                    )
                    CDPDownloader._browser = None

            # 停止旧的 Playwright driver (防止 node 进程累积)
            if CDPDownloader._playwright is not None:
                try:
                    await CDPDownloader._playwright.stop()
                    logger.debug("[cdp] Stopped old playwright driver before reconnect")
                except Exception as e:
                    logger.debug(f"[cdp] Error stopping old playwright: {e}")
                finally:
                    CDPDownloader._playwright = None

            # 需要建立新连接
            cdp_urls = self.settings.cdp_url_list

            # 根据策略排序
            if self.settings.cdp_failover_strategy == "random":
                cdp_urls = random.sample(cdp_urls, len(cdp_urls))

            errors = []

            for cdp_url in cdp_urls:
                # 跳过熔断器打开的实例
                instance_health = self._cdp_health_status.get(cdp_url)
                if instance_health:
                    if instance_health.circuit_state == "OPEN":
                        if time.time() < instance_health.circuit_open_until:
                            logger.debug(
                                f"[cdp] Skipping {cdp_url}: circuit breaker OPEN"
                            )
                            continue

                # 尝试连接
                try:
                    logger.info(f"[cdp] Connecting to {cdp_url}")
                    pw_instance = await async_playwright().start()
                    try:
                        browser = await pw_instance.chromium.connect_over_cdp(
                            cdp_url, timeout=self.settings.cdp_timeout * 1000
                        )
                    except Exception:
                        # 连接失败时立即清理 playwright driver
                        try:
                            await pw_instance.stop()
                        except Exception:
                            pass
                        raise

                    # 更新全局状态
                    CDPDownloader._playwright = pw_instance
                    CDPDownloader._browser = browser

                    # 更新实例健康状态
                    if instance_health:
                        instance_health.is_healthy = True
                        instance_health.consecutive_failures = 0
                        instance_health.circuit_state = "CLOSED"
                        instance_health.last_check_time = time.time()

                    logger.info(f"[cdp] Successfully connected to {cdp_url}")
                    return browser, cdp_url

                except Exception as e:
                    error_msg = f"{cdp_url}: {str(e)}"
                    errors.append(error_msg)
                    logger.warning(f"[cdp] Connection failed: {error_msg}")

                    # 更新实例健康状态
                    if instance_health:
                        instance_health.is_healthy = False
                        instance_health.consecutive_failures += 1
                        instance_health.last_error = str(e)
                        instance_health.last_check_time = time.time()

                        # 触发实例级熔断
                        if (
                            instance_health.consecutive_failures
                            >= self.settings.cdp_circuit_failure_threshold
                        ):
                            instance_health.circuit_state = "OPEN"
                            instance_health.circuit_open_until = (
                                time.time() + self.settings.cdp_circuit_timeout
                            )
                            logger.warning(
                                f"[cdp] Circuit breaker OPEN for {cdp_url}"
                            )

            # 所有实例都失败
            all_errors = "\n".join(f"  - {e}" for e in errors)
            raise DownloaderError(
                message=f"Failed to connect to all CDP instances:\n{all_errors}",
                error_code=ErrorCode.CDP_CONNECTION_FAILED,
                downloader=self.name,
            )

    async def _get_or_create_context(self, browser: Browser) -> BrowserContext:
        """
        获取或创建 BrowserContext。

        优先复用已存在的 context，避免 Chrome 不断打开新窗口。

        Args:
            browser: 浏览器实例

        Returns:
            BrowserContext 实例
        """
        # 尝试复用现有 Context
        if browser.contexts:
            try:
                candidate_context = browser.contexts[0]
                # 有效性检查
                _ = candidate_context.pages
                logger.debug("[cdp] Reusing existing context")
                return candidate_context
            except Exception as e:
                logger.warning(
                    f"[cdp] Existing context invalid: {e}, creating new context"
                )
                try:
                    await candidate_context.close()
                except Exception:
                    pass

        # 创建新 Context
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        logger.debug("[cdp] Created new context")
        return context

    async def _start_background_behavior(
        self,
        page,
        video_url: str,
        video_id: str,
        task_id: str,
        video_duration: Optional[float],
    ) -> bool:
        """
        启动后台人类行为模拟任务。

        Args:
            page: Playwright Page 实例
            video_url: 视频 URL
            video_id: 视频 ID
            task_id: 任务 ID
            video_duration: 视频时长

        Returns:
            bool: 是否启动了后台任务
        """
        if not self.settings.cdp_human_behavior_enabled or self.settings.cdp_quick_mode:
            # 快速模式：直接关闭页面
            await page.close()
            return False

        task = asyncio.create_task(
            self._behavior_simulator.background_human_behavior(
                page, video_url, video_id, task_id, video_duration
            )
        )
        # 强引用 + 完成自动 discard，防止 GC 中断
        self._track_background_task(task)

        def handle_task_exception(t):
            if t.cancelled():
                logger.debug(f"[cdp] Background task cancelled for {video_id}")
                return
            try:
                t.result()
            except asyncio.CancelledError:
                logger.debug(f"[cdp] Background task cancelled for {video_id}")
            except Exception as e:
                logger.error(f"[cdp] Background task failed for {video_id}: {e}")

        task.add_done_callback(handle_task_exception)
        return True

    async def _get_pot_token_safe(self, video_id: str) -> Optional[str]:
        """
        安全获取 PO Token（失败不抛出异常）。

        集成 PotProviderHealthTracker：
        - pot-provider 不可用时跳过请求（避免无谓等待）
        - 成功/失败时更新健康状态

        Args:
            video_id: 视频 ID

        Returns:
            PO Token 或 None
        """
        if not self.settings.cdp_enable_pot_token:
            return None

        from src.downloaders.pot_health import PotProviderHealthTracker
        tracker = PotProviderHealthTracker.get_instance()

        # pot-provider 不可用时跳过（冷却期内不浪费时间）
        if not tracker.is_available():
            logger.debug(
                f"[cdp] Skipping poToken for {video_id}: "
                f"provider unavailable (failures: {tracker.consecutive_failures})"
            )
            return None

        try:
            pot_token = await self._get_pot_token(video_id)
            if pot_token:
                tracker.record_success()
                return pot_token
            else:
                tracker.record_failure()
                logger.warning(f"[cdp] Failed to get poToken for {video_id}")
                return None
        except Exception as e:
            tracker.record_failure()
            logger.warning(f"[cdp] poToken acquisition error: {e}")
            return None

    # ========== Cookie 同步方法 ==========

    async def _sync_cookie_to_shared_location(self, cookie_file: Path) -> None:
        """
        将 CDP 导出的 Cookie 同步到共享位置。

        让 ytdlp 下载器也能使用最新的 Cookie，提高降级场景的成功率。

        工作流程：
        1. 复制临时 Cookie 文件到共享位置（data/latest_cookies.txt）
        2. 设置文件权限为 600（仅所有者可读写，Unix 系统）
        3. 记录同步日志

        Args:
            cookie_file: CDP 导出的临时 Cookie 文件路径

        副作用：
            - 创建/覆盖 data/latest_cookies.txt
            - 同步失败不影响主流程（仅记录警告日志）
        """
        try:
            import os
            import shutil

            # 共享 Cookie 文件路径
            shared_cookie_path = self.settings.data_dir / "latest_cookies.txt"

            # 确保父目录存在
            shared_cookie_path.parent.mkdir(parents=True, exist_ok=True)

            # 复制文件（保留原临时文件）
            shutil.copy2(cookie_file, shared_cookie_path)

            # 设置文件权限（仅所有者可读写）
            if os.name != "nt":
                os.chmod(shared_cookie_path, 0o600)

            logger.info(
                f"[cdp] Synced fresh cookies to shared location: {shared_cookie_path}",
                extra={
                    "event": "cdp_cookie_synced",
                    "cookie_age_seconds": 0,
                    "shared_path": str(shared_cookie_path),
                }
            )

        except Exception as e:
            # 同步失败不影响主流程
            logger.warning(
                f"[cdp] Failed to sync cookies to shared location: {e}",
                extra={
                    "event": "cdp_cookie_sync_failed",
                    "error": str(e),
                }
            )

    # ========== POT Token 方法 ==========

    async def _get_pot_token(self, video_id: str) -> Optional[str]:
        """
        从 POT Provider 获取 poToken。

        Args:
            video_id: 视频 ID

        Returns:
            poToken 字符串，或 None（如果获取失败）
        """
        if not self.settings.pot_server_url:
            logger.debug("[cdp] POT_SERVER_URL not configured")
            return None

        try:
            import httpx

            pot_url = f"{self.settings.pot_server_url.rstrip('/')}/get_pot"
            payload = {
                "client": "web",
                "video_id": video_id,
            }

            timeout = httpx.Timeout(10.0, connect=5.0)

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(pot_url, json=payload)
                response.raise_for_status()

                data = response.json()
                pot_token = data.get("po_token") or data.get("poToken")

                if pot_token:
                    logger.debug(f"[cdp] Successfully got poToken for {video_id}")
                    return str(pot_token)
                else:
                    logger.warning(f"[cdp] POT provider returned no token: {data}")
                    return None

        except Exception as e:
            logger.warning(f"[cdp] Failed to get poToken from {self.settings.pot_server_url}: {e}")
            return None

    # ========== 熔断器管理 ==========

    async def _update_circuit_breaker(self, success: bool):
        """
        更新熔断器状态。

        状态机：
        CLOSED → (连续失败 3 次) → OPEN
        OPEN → (等待 30 分钟) → HALF_OPEN
        HALF_OPEN → (成功 2 次) → CLOSED
        HALF_OPEN → (失败 1 次) → OPEN
        """
        if success:
            if CDPDownloader._circuit_breaker_state == "HALF_OPEN":
                # 半开状态成功
                CDPDownloader._health_check_failures = max(
                    0, CDPDownloader._health_check_failures - 1
                )
                if (
                    CDPDownloader._health_check_failures
                    <= self.settings.cdp_circuit_half_open_success
                ):
                    CDPDownloader._circuit_breaker_state = "CLOSED"
                    CDPDownloader._health_check_failures = 0
                    logger.info("[cdp] Circuit breaker CLOSED (recovered)")

                    # 发送恢复通知
                    await self._notify_cdp_recovered()
            else:
                # 正常状态成功：重置失败计数
                CDPDownloader._health_check_failures = 0
        else:
            # 失败
            CDPDownloader._health_check_failures += 1

            if CDPDownloader._circuit_breaker_state == "HALF_OPEN":
                # 半开状态失败：重新打开
                CDPDownloader._circuit_breaker_state = "OPEN"
                CDPDownloader._circuit_open_until = (
                    time.time() + self.settings.cdp_circuit_timeout
                )
                logger.warning("[cdp] Circuit breaker re-OPEN from HALF_OPEN")

            elif (
                CDPDownloader._health_check_failures
                >= self.settings.cdp_circuit_failure_threshold
            ):
                # 连续失败达到阈值：打开熔断器
                CDPDownloader._circuit_breaker_state = "OPEN"
                CDPDownloader._circuit_open_until = (
                    time.time() + self.settings.cdp_circuit_timeout
                )
                logger.warning(
                    f"[cdp] Circuit breaker OPEN (failures: {CDPDownloader._health_check_failures})"
                )

                # 发送熔断器打开通知
                await self._notify_circuit_breaker_open(
                    CDPDownloader._health_check_failures,
                    CDPDownloader._circuit_open_until,
                )

    async def _handle_download_error(
        self,
        error: Exception,
        video_id: str,
        task_id: str,
    ):
        """错误处理。"""
        logger.error(f"[cdp] Download error for {video_id}: {error}")

    # ========== 通知方法 ==========

    def _get_notify_service(self) -> Optional[NotificationService]:
        """
        获取通知服务实例（懒加载）。

        Returns:
            NotificationService 或 None（如果未配置）
        """
        if not NOTIFICATION_AVAILABLE:
            return None

        if self._notify_service is None:
            try:
                self._notify_service = NotificationService(self.settings)
            except Exception as e:
                logger.warning(f"[cdp] Failed to initialize NotificationService: {e}")
                return None

        return self._notify_service

    async def _notify_connection_failure(
        self,
        error: str,
        context: str = "",
    ) -> None:
        """
        发送 CDP 连接失败通知（带频率限制）。

        Args:
            error: 错误信息
            context: 上下文（如 "health_check", "download"）
        """
        notify_service = self._get_notify_service()
        if not notify_service:
            return

        # 频率限制：使用 error hash 作为 key
        cache_key = f"cdp_conn_fail:{hash(error)}"
        last_notify = CDPDownloader._notification_cache.get(cache_key, 0)
        cooldown = self.settings.cdp_notify_cooldown

        if time.time() - last_notify < cooldown:
            logger.debug(f"[cdp] Connection failure notification throttled: {error[:50]}")
            return

        # 发送通知
        try:
            # 获取使用的 CDP URL（如果可能）
            cdp_url = (
                self.settings.cdp_url_list[0]
                if self.settings.cdp_url_list
                else "unknown"
            )

            await notify_service.notify_cdp_connection_failed(
                error=error,
                cdp_url=cdp_url,
            )

            CDPDownloader._notification_cache[cache_key] = time.time()

            logger.info(
                f"[cdp] Connection failure notification sent",
                extra={
                    "event": "cdp_connection_failed",
                    "context": context,
                    "cdp_url": cdp_url,
                },
            )

        except Exception as e:
            logger.error(f"[cdp] Failed to send connection failure notification: {e}")

    async def _notify_circuit_breaker_open(
        self,
        consecutive_failures: int,
        open_until_timestamp: float,
    ) -> None:
        """
        发送 CDP 熔断器打开通知。

        Args:
            consecutive_failures: 连续失败次数
            open_until_timestamp: 熔断结束时间（Unix 时间戳）
        """
        notify_service = self._get_notify_service()
        if not notify_service:
            return

        try:
            await notify_service.notify_cdp_circuit_breaker_open(
                consecutive_failures=consecutive_failures,
                open_until_timestamp=open_until_timestamp,
            )

            logger.info(
                "[cdp] Circuit breaker open notification sent",
                extra={
                    "event": "cdp_circuit_breaker_open",
                    "consecutive_failures": consecutive_failures,
                    "open_until": open_until_timestamp,
                },
            )

        except Exception as e:
            logger.error(f"[cdp] Failed to send circuit breaker notification: {e}")

    async def _notify_cdp_recovered(self) -> None:
        """
        发送 CDP 恢复通知。
        """
        notify_service = self._get_notify_service()
        if not notify_service:
            return

        try:
            await notify_service.notify_cdp_recovered()

            logger.info(
                "[cdp] CDP recovery notification sent",
                extra={"event": "cdp_recovered"},
            )

        except Exception as e:
            logger.error(f"[cdp] Failed to send CDP recovery notification: {e}")
