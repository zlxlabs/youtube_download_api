"""
CDP (Chrome DevTools Protocol) 下载器实现。

基于真实浏览器的 YouTube 音频下载器，通过以下技术降低 403 风控：
- 真实浏览器 Cookies（每次刷新）
- CDP 捕获的真实 Headers
- curl_cffi TLS 指纹模拟
- 可选 poToken 支持

特性：
- 多客户端并发隔离（共享 Browser + 独立 Context）
- 多 CDP 实例故障转移
- 三层降级下载（curl_cffi → httpx → yt-dlp）
- 熔断器保护
"""

import asyncio
import random
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import httpx

try:
    from playwright.async_api import Browser, BrowserContext, async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None
    BrowserContext = None

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.base import BaseDownloader
from src.downloaders.cdp_models import (
    AudioInfo,
    CDPHealthStatus,
    CDPInstanceHealth,
)
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, DownloaderType, VideoMetadata
from src.utils.logger import logger


class CDPDownloader(BaseDownloader):
    """
    基于 Chrome DevTools Protocol 的 YouTube 音频下载器。

    工作流程：
    1. 连接到外部 Chrome（共享 Browser）
    2. 创建独立 BrowserContext（每任务独立）
    3. 导出 Cookies（刷新登录态）
    4. 提取真实 Headers（CDP 拦截请求）
    5. 使用 yt-dlp + cookies 提取音频 URL
    6. 下载音频（curl_cffi + 真实 headers → httpx → ytdlp）
    7. 清理 Context 和临时文件

    并发安全性：
    - 共享 Browser 实例（所有任务共享同一个 CDP 连接）
    - 每个任务创建独立的 BrowserContext（完全隔离）
    - 通过 asyncio.Lock 保证 Browser 连接的线程安全
    """

    # ========== 类级别共享状态 ==========
    _browser: Optional[Browser] = None
    _browser_lock: asyncio.Lock = None  # 延迟初始化
    _last_health_check: float = 0
    _cdp_health_status: Dict[str, CDPInstanceHealth] = {}

    # 熔断器状态（全局）
    _circuit_breaker_state: str = "CLOSED"  # CLOSED/OPEN/HALF_OPEN
    _circuit_open_until: float = 0
    _health_check_failures: int = 0

    def __init__(self, settings: Settings):
        """
        初始化 CDP 下载器。

        Args:
            settings: 应用配置
        """
        self.settings = settings

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
        下载资源（仅支持音频）。

        CDP 下载器仅负责音频下载，不支持字幕。
        如果 include_audio=False，直接返回不支持。

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕（CDP 不支持）

        Returns:
            DownloaderResult

        Raises:
            DownloaderError: 下载失败
        """
        logger.info(
            f"[cdp] Starting download for {video_id}: "
            f"audio={include_audio}, transcript={include_transcript}"
        )

        # CDP 仅支持音频下载
        if not include_audio:
            logger.debug("[cdp] CDP does not support transcript-only mode")
            raise DownloaderError(
                message="CDP downloader only supports audio download",
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader=self.name,
            )

        # 生成任务 ID（用于临时文件命名）
        task_id = f"cdp_{video_id}_{int(time.time())}"

        try:
            # 1. 获取共享的 Browser（多实例故障转移）
            browser, cdp_url = await self._get_browser()
            logger.debug(f"[cdp] Connected to browser at {cdp_url}")

            # 2. 获取或创建 BrowserContext（优先复用已存在的 context）
            # 注意：复用 context 避免 Chrome 不断打开新窗口
            context_is_reused = False
            if browser.contexts:
                context = browser.contexts[0]
                context_is_reused = True
                logger.debug(f"[cdp] Reusing existing context")
            else:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    viewport={"width": 1920, "height": 1080},
                )
                logger.debug(f"[cdp] Created new context")

            context.set_default_timeout(30000)

            try:
                # 3. 导出 Cookies（刷新登录态）
                cookie_file = await self._export_cookies(
                    context, video_url, video_id, task_id
                )

                # 4. 提取真实 Headers（CDP 拦截请求）
                headers = await self._extract_request_headers(
                    context, video_url, video_id
                )

                # 5. 使用 yt-dlp + cookies 提取音频 URL
                audio_info = await self._extract_audio_url(
                    video_url, video_id, cookie_file, task_id
                )

                # 6. 下载音频
                audio_path = await self._download_audio(
                    audio_info, video_id, task_id, output_dir, headers
                )

                # 7. 构造成功结果
                metadata = VideoMetadata(
                    video_id=video_id,
                    title=audio_info.title,
                    source_downloader=self.name,
                )

                result = DownloaderResult(
                    success=True,
                    downloader=self.name,
                    video_metadata=metadata,
                    audio_path=audio_path,
                    has_transcript=False,
                )

                logger.info(
                    f"[cdp] Successfully downloaded {video_id}: "
                    f"size={audio_path.stat().st_size} bytes"
                )

                # 更新熔断器（成功）
                await self._update_circuit_breaker(success=True)

                return result

            finally:
                # 清理 Context（仅关闭新创建的 context，不关闭复用的）
                if not context_is_reused:
                    await context.close()
                    logger.debug("[cdp] Closed created context")

                # 清理临时 cookie 文件
                cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
                if cookie_file.exists():
                    cookie_file.unlink()
                    logger.debug(f"[cdp] Cleaned up cookie file: {cookie_file}")

        except Exception as e:
            # 更新熔断器（失败）
            await self._update_circuit_breaker(success=False)

            # 处理错误
            await self._handle_download_error(e, video_id, task_id)
            raise

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

        不触发熔断的错误：
        - 视频特定错误（如无音频 URL）
        - Cookie 导出失败（可能未登录）
        """
        if isinstance(error, DownloaderError):
            if error.error_code in [
                ErrorCode.CDP_CONNECTION_FAILED,
                ErrorCode.CDP_CONNECTION_TIMEOUT,
                ErrorCode.CDP_CHROME_NOT_READY,
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
        async with CDPDownloader._browser_lock:
            # 如果已有连接且健康，直接返回
            if CDPDownloader._browser is not None:
                # TODO: 可以添加健康检查
                # 暂时假设连接总是有效的
                # 返回第一个健康的 URL（简化版）
                for url in self.settings.cdp_url_list:
                    if self._cdp_health_status[url].is_healthy:
                        return CDPDownloader._browser, url

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
                    playwright = await async_playwright().start()
                    browser = await playwright.chromium.connect_over_cdp(
                        cdp_url, timeout=self.settings.cdp_timeout * 1000
                    )

                    # 更新全局状态
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

    # ========== Cookie 管理 ==========

    async def _export_cookies(
        self,
        context: BrowserContext,
        video_url: str,
        video_id: str,
        task_id: str,
    ) -> Path:
        """
        导出 Cookies（每次导出新 cookie，优先降低 403）。

        流程：
        1. 在 Context 中创建新 Page
        2. 访问视频页面（刷新登录态）
        3. 等待页面加载完成
        4. 可选：触发视频播放（刷新 session）
        5. 使用 CDP 获取所有 cookies
        6. 过滤 YouTube cookies
        7. 转换为 Netscape 格式
        8. 写入临时文件

        Returns:
            Path: cookies 文件路径
        """
        logger.debug(f"[cdp] Exporting cookies for {video_id}")

        page = await context.new_page()
        try:
            # 访问视频页面
            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)

            # 等待视频播放器加载
            try:
                await page.wait_for_selector("#movie_player", timeout=15000)
            except Exception:
                logger.warning("[cdp] Video player not found, continuing anyway")

            # 可选：触发视频播放（静音）
            try:
                await page.evaluate("""() => {
                    const video = document.querySelector('video');
                    if (!video) return;
                    video.muted = true;
                    const p = video.play();
                    if (p && p.catch) p.catch(() => {});
                }""")
                await asyncio.sleep(2)  # 等待播放触发请求
            except Exception:
                logger.debug("[cdp] Could not trigger playback")

            # 使用 CDP 获取 cookies
            cdp_session = await context.new_cdp_session(page)
            cookies_result = await cdp_session.send("Network.getAllCookies")
            cookies = cookies_result.get("cookies", [])

            # 过滤并转换为 Netscape 格式
            netscape_content = self._cookies_to_netscape(cookies)

            # 写入临时文件
            cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"
            cookie_file.parent.mkdir(parents=True, exist_ok=True)
            cookie_file.write_text(netscape_content, encoding="utf-8")

            logger.info(f"[cdp] Exported {len(cookies)} cookies to {cookie_file}")
            return cookie_file

        finally:
            await page.close()

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

    # ========== Headers 提取 ==========

    async def _extract_request_headers(
        self,
        context: BrowserContext,
        video_url: str,
        video_id: str,
    ) -> Dict[str, str]:
        """
        通过 CDP 拦截真实请求，提取 Headers。

        原理：
        1. 监听 Network 请求
        2. 触发视频播放
        3. 捕获 googlevideo.com 的音频请求
        4. 提取真实的 Headers

        Returns:
            Dict[str, str]: 真实请求的 Headers（如果未捕获则返回默认值）
        """
        logger.debug(f"[cdp] Extracting request headers for {video_id}")

        captured_headers = {}
        headers_captured = asyncio.Event()

        page = await context.new_page()

        try:
            # 定义请求拦截器
            async def capture_request(request):
                # 只捕获 googlevideo.com 的请求
                if "googlevideo.com" in request.url:
                    nonlocal captured_headers
                    captured_headers = request.headers
                    headers_captured.set()

            # 监听请求
            page.on("request", capture_request)

            # 访问视频页面
            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)

            # 触发视频播放
            try:
                await page.wait_for_selector("video", timeout=15000)
                await page.evaluate("""() => {
                    const video = document.querySelector('video');
                    if (!video) return;
                    video.muted = true;
                    const p = video.play();
                    if (p && p.catch) p.catch(() => {});
                }""")
            except Exception:
                logger.debug("[cdp] Could not trigger playback for headers extraction")

            # 等待捕获（最多 10 秒）
            try:
                await asyncio.wait_for(headers_captured.wait(), timeout=10)
                logger.info(f"[cdp] Captured {len(captured_headers)} headers")
            except asyncio.TimeoutError:
                logger.warning("[cdp] Failed to capture headers, using defaults")

            # 返回捕获的 headers 或默认值
            if captured_headers:
                return captured_headers
            else:
                return {
                    "referer": "https://www.youtube.com/",
                    "origin": "https://www.youtube.com",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }

        finally:
            await page.close()

    # ========== 音频 URL 提取 ==========

    async def _extract_audio_url(
        self,
        video_url: str,
        video_id: str,
        cookie_file: Path,
        task_id: str,
    ) -> AudioInfo:
        """
        使用 yt-dlp + cookies 提取音频信息（避免触发下载）。

        Returns:
            AudioInfo: 音频信息

        Raises:
            DownloaderError: 提取失败
        """
        if not YTDLP_AVAILABLE:
            raise DownloaderError(
                message="yt-dlp not available",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.name,
            )

        logger.debug(f"[cdp] Extracting audio URL for {video_id}")

        ydl_opts = {
            "cookiefile": str(cookie_file),
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,  # 关键：禁止下载
            "simulate": False,
            "extract_flat": False,
            "no_color": True,
        }

        # 可选：注入 poToken
        if self.settings.cdp_enable_pot_token:
            # TODO: 从 POT provider 获取 token
            # ydl_opts['extractor_args'] = {'youtube': {'po_token': [token]}}
            pass

        try:
            # 在线程池中执行（避免阻塞）
            info = await asyncio.to_thread(self._ytdlp_extract_info, video_url, ydl_opts)

            if not info:
                raise DownloaderError(
                    message="yt-dlp returned no info",
                    error_code=ErrorCode.CDP_YTDLP_FAILED,
                    downloader=self.name,
                )

            # 提取音频 URL
            audio_url = info.get("url")
            if not audio_url:
                raise DownloaderError(
                    message="No audio URL found in yt-dlp output",
                    error_code=ErrorCode.CDP_NO_AUDIO_URL,
                    downloader=self.name,
                )

            # 构造 AudioInfo
            audio_info = AudioInfo(
                url=audio_url,
                itag=self._parse_itag(audio_url),
                mime_type=info.get("ext", "m4a"),
                title=info.get("title", f"youtube_{video_id}"),
                filesize=info.get("filesize") or info.get("filesize_approx"),
                ext=info.get("ext", "m4a"),
            )

            logger.info(
                f"[cdp] Extracted audio URL: itag={audio_info.itag}, "
                f"size={audio_info.filesize or 'unknown'}, ext={audio_info.ext}"
            )

            return audio_info

        except Exception as e:
            if isinstance(e, DownloaderError):
                raise
            raise DownloaderError(
                message=f"yt-dlp extraction failed: {str(e)}",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.name,
            )

    def _ytdlp_extract_info(self, video_url: str, ydl_opts: dict) -> dict:
        """在同步上下文中执行 yt-dlp 提取（用于线程池）。"""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(video_url, download=False)

    def _parse_itag(self, url: str) -> Optional[int]:
        """从 URL 中解析 itag。"""
        try:
            query = parse_qs(urlparse(url).query)
            itag = query.get("itag", [None])[0]
            return int(itag) if itag else None
        except Exception:
            return None

    # ========== 音频下载 ==========

    async def _download_audio(
        self,
        audio_info: AudioInfo,
        video_id: str,
        task_id: str,
        output_dir: Path,
        headers: Dict[str, str],
    ) -> Path:
        """
        下载音频（三层降级，使用真实 Headers）。

        正确顺序（从最优到兜底）：
        1. curl_cffi 下载（TLS 指纹模拟 + 真实 Headers）- 最优
        2. 失败 → httpx 下载（真实 Headers）- 降级
        3. 失败 → yt-dlp 直接下载（使用 cookies）- 兜底

        Returns:
            Path: 音频文件路径

        Raises:
            DownloaderError: 下载失败
        """
        logger.info(f"[cdp] Downloading audio for {video_id}")

        # 生成目标文件名
        safe_title = self._sanitize_filename(audio_info.title)
        filename = f"{safe_title}_itag{audio_info.itag or 'na'}.{audio_info.ext}"
        target_path = output_dir / filename

        # 收集所有错误
        errors = []

        # 1. 优先：curl_cffi 下载（TLS 指纹模拟 + 真实 Headers）
        if self.settings.cdp_use_curl_cffi:
            try:
                success = await self._download_with_curl_cffi(
                    url=audio_info.url,
                    target_path=target_path,
                    expected_size=audio_info.filesize,
                    headers=headers,
                )
                if success:
                    logger.info(f"[cdp] Downloaded via curl_cffi: {target_path}")
                    return target_path
            except DownloaderError as e:
                # 403 错误：停止尝试，直接抛出（触发 IP 熔断）
                if e.error_code == ErrorCode.CDP_DOWNLOAD_403:
                    logger.error("[cdp] HTTP 403 detected, stopping download attempts")
                    raise
                errors.append(f"curl_cffi: {e.message}")
                logger.warning(f"[cdp] curl_cffi download failed: {e.message}")
            except Exception as e:
                errors.append(f"curl_cffi: {str(e)}")
                logger.warning(f"[cdp] curl_cffi download failed: {e}")

        # 2. 降级：httpx 下载（真实 Headers）
        try:
            success = await self._download_with_httpx(
                url=audio_info.url,
                target_path=target_path,
                expected_size=audio_info.filesize,
                headers=headers,
            )
            if success:
                logger.info(f"[cdp] Downloaded via httpx: {target_path}")
                return target_path
        except DownloaderError as e:
            # 403 错误：停止尝试，直接抛出（触发 IP 熔断）
            if e.error_code == ErrorCode.CDP_DOWNLOAD_403:
                logger.error("[cdp] HTTP 403 detected, stopping download attempts")
                raise
            errors.append(f"httpx: {e.message}")
            logger.warning(f"[cdp] httpx download failed: {e.message}")
        except Exception as e:
            errors.append(f"httpx: {str(e)}")
            logger.warning(f"[cdp] httpx download failed: {e}")

        # 3. 兜底：yt-dlp 直接下载（使用 cookies）
        logger.warning("[cdp] Falling back to yt-dlp download")
        try:
            # 获取 cookie 文件路径
            cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"

            ytdlp_path = await self._download_with_ytdlp(
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                cookie_file=cookie_file,
                output_dir=output_dir,
                expected_filename=filename,
            )
            if ytdlp_path and ytdlp_path.exists():
                logger.info(f"[cdp] Downloaded via yt-dlp: {ytdlp_path}")
                return ytdlp_path
        except Exception as e:
            errors.append(f"ytdlp: {str(e)}")
            logger.error(f"[cdp] yt-dlp download failed: {e}")

        # 所有方法都失败
        all_errors = "\n".join(f"  - {e}" for e in errors)
        raise DownloaderError(
            message=f"All download methods failed:\n{all_errors}",
            error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
            downloader=self.name,
        )

    async def _download_with_httpx(
        self,
        url: str,
        target_path: Path,
        expected_size: Optional[int],
        headers: Dict[str, str],
    ) -> bool:
        """
        httpx 下载（支持断点续传）。

        Returns:
            bool: 下载是否成功
        """
        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        resume_from = temp_path.stat().st_size if temp_path.exists() else 0

        # 如果已下载完成
        if expected_size and resume_from >= expected_size:
            temp_path.replace(target_path)
            return True

        # 设置 Range header（断点续传）
        request_headers = headers.copy()
        if resume_from:
            request_headers["Range"] = f"bytes={resume_from}-"

        # 下载
        timeout = httpx.Timeout(10.0, read=120.0)
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", url, headers=request_headers, timeout=timeout, follow_redirects=True
            ) as resp:
                if resp.status_code == 403:
                    # 403 错误：触发 IP 熔断
                    raise DownloaderError(
                        message=f"HTTP 403 for {url}",
                        error_code=ErrorCode.CDP_DOWNLOAD_403,
                        downloader=self.name,
                        http_status_code=403,
                        stop_fallback=True,  # 停止降级
                    )

                if resp.status_code not in (200, 206):
                    raise DownloaderError(
                        message=f"HTTP {resp.status_code}",
                        error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
                        downloader=self.name,
                        http_status_code=resp.status_code,
                    )

                # 写入文件
                mode = "ab" if resume_from else "wb"
                with temp_path.open(mode) as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)

        # 校验文件大小
        final_size = temp_path.stat().st_size if temp_path.exists() else 0
        if expected_size and final_size < expected_size * 0.95:  # 允许 5% 误差
            logger.warning(
                f"[cdp] Size mismatch: got {final_size}, expected {expected_size}"
            )
            return False

        # 移动到最终位置
        temp_path.replace(target_path)
        return True

    def _sanitize_filename(self, text: str) -> str:
        """清理文件名中的非法字符。"""
        text = re.sub(r'[\\/:*?"<>|]+', "_", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:120] if text else "youtube_audio"

    async def _download_with_curl_cffi(
        self,
        url: str,
        target_path: Path,
        expected_size: Optional[int],
        headers: Dict[str, str],
    ) -> bool:
        """
        curl_cffi 下载（TLS 指纹模拟）。

        Returns:
            bool: 下载是否成功
        """
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning("[cdp] curl_cffi not installed, skipping")
            return False

        logger.debug("[cdp] Attempting download with curl_cffi")

        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        resume_from = temp_path.stat().st_size if temp_path.exists() else 0

        # 如果已下载完成
        if expected_size and resume_from >= expected_size:
            temp_path.replace(target_path)
            return True

        # 设置 Range header（断点续传）
        request_headers = headers.copy()
        if resume_from:
            request_headers["Range"] = f"bytes={resume_from}-"

        try:
            # 使用 curl_cffi 流式下载（Chrome 120 TLS 指纹）
            # 注意：必须使用流式下载，否则大文件会超时且丢失已下载数据
            def _curl_cffi_download():
                """同步流式下载（在线程池中执行）"""
                with curl_requests.get(
                    url,
                    headers=request_headers,
                    impersonate="chrome120",
                    verify=False,
                    timeout=300,  # 增加到 5 分钟（流式下载）
                    allow_redirects=True,
                    stream=True,  # ← 关键：启用流式下载
                ) as response:
                    # 检查状态码
                    if response.status_code == 403:
                        raise DownloaderError(
                            message=f"HTTP 403 for {url}",
                            error_code=ErrorCode.CDP_DOWNLOAD_403,
                            downloader=self.name,
                            http_status_code=403,
                            stop_fallback=True,
                        )

                    if response.status_code not in (200, 206):
                        raise DownloaderError(
                            message=f"HTTP {response.status_code}",
                            error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
                            downloader=self.name,
                            http_status_code=response.status_code,
                        )

                    # 流式写入文件
                    mode = "ab" if resume_from else "wb"
                    with temp_path.open(mode) as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

            # 在线程池中执行（避免阻塞）
            await asyncio.to_thread(_curl_cffi_download)

            # 校验文件大小
            final_size = temp_path.stat().st_size if temp_path.exists() else 0
            if expected_size and final_size < expected_size * 0.95:
                logger.warning(
                    f"[cdp] Size mismatch: got {final_size}, expected {expected_size}"
                )
                return False

            # 移动到最终位置
            temp_path.replace(target_path)
            return True

        except DownloaderError:
            raise
        except Exception as e:
            logger.warning(f"[cdp] curl_cffi download error: {e}")
            return False

    async def _download_with_ytdlp(
        self,
        video_url: str,
        cookie_file: Path,
        output_dir: Path,
        expected_filename: str,
    ) -> Optional[Path]:
        """
        yt-dlp 直接下载（兜底方案）。

        使用 yt-dlp 直接下载音频文件（使用 cookies）。

        Args:
            video_url: YouTube 视频 URL
            cookie_file: cookies 文件路径
            output_dir: 输出目录
            expected_filename: 预期文件名

        Returns:
            Path: 下载的文件路径，失败返回 None
        """
        if not YTDLP_AVAILABLE:
            logger.error("[cdp] yt-dlp not available")
            return None

        logger.info("[cdp] Downloading with yt-dlp")

        # 构造输出模板（使用原始文件名）
        outtmpl = str(output_dir / expected_filename.replace(".webm", ".%(ext)s").replace(".m4a", ".%(ext)s"))

        ydl_opts = {
            "cookiefile": str(cookie_file),
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": outtmpl,
            "noplaylist": True,
        }

        try:
            # 在线程池中执行（避免阻塞）
            downloaded_path = await asyncio.to_thread(
                self._ytdlp_download_sync, video_url, ydl_opts
            )
            return downloaded_path
        except Exception as e:
            logger.error(f"[cdp] yt-dlp download failed: {e}")
            return None

    def _ytdlp_download_sync(self, video_url: str, ydl_opts: dict) -> Optional[Path]:
        """在同步上下文中执行 yt-dlp 下载（用于线程池）。"""
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                if not info:
                    return None
                downloaded_file = Path(ydl.prepare_filename(info))
                return downloaded_file
        except Exception as e:
            logger.error(f"[cdp] yt-dlp sync download error: {e}")
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

    async def _handle_download_error(
        self,
        error: Exception,
        video_id: str,
        task_id: str,
    ):
        """错误处理。"""
        logger.error(f"[cdp] Download error for {video_id}: {error}")
