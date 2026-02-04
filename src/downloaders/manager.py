"""
下载器管理器。

负责管理多个下载器，实现降级策略和熔断保护。
"""

import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional

from src.config import Settings
from src.core.downloader import DownloadCancelledError
from src.db.database import Database
from src.downloaders.base import BaseDownloader
from src.downloaders.cdp import CDPDownloader
from src.downloaders.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata
from src.downloaders.tikhub_downloader import TikHubDownloader
from src.downloaders.youtube_data_api_downloader import YoutubeDataApiDownloader
from src.downloaders.ytdlp_downloader import YtdlpDownloader
from src.utils.logger import logger


class DownloaderStats:
    """
    下载器统计数据。

    记录各下载器的成功/失败次数，用于监控和告警。
    注意：仅用于统计，不影响路由决策。
    """

    def __init__(self):
        """初始化统计数据。"""
        self.stats: Dict[str, Dict[str, int]] = {}

    def record_success(self, downloader: str) -> None:
        """记录成功。"""
        if downloader not in self.stats:
            self.stats[downloader] = {"success": 0, "failure": 0, "total": 0}

        self.stats[downloader]["success"] += 1
        self.stats[downloader]["total"] += 1

    def record_failure(self, downloader: str) -> None:
        """记录失败。"""
        if downloader not in self.stats:
            self.stats[downloader] = {"success": 0, "failure": 0, "total": 0}

        self.stats[downloader]["failure"] += 1
        self.stats[downloader]["total"] += 1

    def get_success_rate(self, downloader: str) -> float:
        """
        获取成功率。

        Returns:
            成功率（0.0-1.0），如果没有数据返回 0.0
        """
        if downloader not in self.stats:
            return 0.0

        total = self.stats[downloader]["total"]
        if total == 0:
            return 0.0

        return self.stats[downloader]["success"] / total

    def get_summary(self) -> Dict[str, dict]:
        """
        获取统计摘要。

        Returns:
            包含所有下载器统计的字典
        """
        summary = {}
        for downloader, data in self.stats.items():
            summary[downloader] = {
                **data,
                "success_rate": self.get_success_rate(downloader),
            }
        return summary


class DownloaderManager:
    """
    下载器管理器。

    管理多个下载器，实现：
    1. 按优先级顺序尝试
    2. 熔断器保护
    3. 自动降级
    4. 统计监控
    """

    def __init__(self, settings: Settings, db: Optional[Database] = None):
        """
        初始化下载器管理器。

        Args:
            settings: 应用配置
            db: 数据库实例（可选，用于元数据缓存）
        """
        self.settings = settings
        self.db = db
        self.downloaders = self._init_downloaders()
        self.circuit_breakers = self._init_circuit_breakers()
        self.stats = DownloaderStats()

        # 并发锁：防止同一视频的重复 API 调用
        self._metadata_locks: Dict[str, asyncio.Lock] = {}

        logger.info(
            f"DownloaderManager initialized with {len(self.downloaders)} downloader(s): "
            f"{[d.name for d in self.downloaders]}"
        )

    def _init_downloaders(self) -> List[BaseDownloader]:
        """
        初始化下载器列表。

        从所有优先级配置中收集下载器名称（去重），然后初始化。
        这样确保元数据专用下载器（如 youtube_data_api）也能被加载。

        Returns:
            下载器列表
        """
        downloaders: List[BaseDownloader] = []

        # 从多个配置中收集下载器名称
        priority_configs = [
            getattr(self.settings, "metadata_priority", "ytdlp,tikhub"),
            getattr(self.settings, "transcript_only_priority", "tikhub,ytdlp"),
            getattr(self.settings, "audio_download_priority", "ytdlp,tikhub"),
            getattr(self.settings, "downloader_priority", "ytdlp,tikhub"),  # 兜底配置
        ]

        # 收集所有下载器名称（去重，保持顺序）
        seen = set()
        priority_list = []
        for config in priority_configs:
            for name in config.split(","):
                name = name.strip()
                if name and name not in seen:
                    seen.add(name)
                    priority_list.append(name)

        logger.info(f"Initializing downloaders: {priority_list}")

        for name in priority_list:
            try:
                if name == "cdp":
                    downloader = CDPDownloader(self.settings)
                    if downloader.is_available:
                        downloaders.append(downloader)
                        logger.info(f"  ✓ {name} enabled (CDP configured)")
                    else:
                        logger.warning(f"  ✗ {name} not available (CDP not enabled or Playwright not installed)")

                elif name == "ytdlp":
                    downloader = YtdlpDownloader(self.settings)
                    if downloader.is_available:
                        downloaders.append(downloader)
                        logger.info(f"  ✓ {name} enabled")
                    else:
                        logger.warning(f"  ✗ {name} not available (skipped)")

                elif name == "tikhub":
                    downloader = TikHubDownloader(self.settings)
                    if downloader.is_available:
                        downloaders.append(downloader)
                        logger.info(f"  ✓ {name} enabled (API key configured)")
                    else:
                        logger.warning(f"  ✗ {name} not available (API key not configured)")

                elif name == "youtube_data_api":
                    downloader = YoutubeDataApiDownloader(self.settings)
                    if downloader.is_available:
                        downloaders.append(downloader)
                        logger.info(f"  ✓ {name} enabled (API key configured)")
                    else:
                        logger.warning(f"  ✗ {name} not available (API key not configured)")

                else:
                    logger.warning(f"  ? Unknown downloader: {name} (skipped)")

            except Exception as e:
                logger.error(f"  ✗ Failed to initialize {name}: {e}")

        if not downloaders:
            logger.warning("No downloaders available! Please check configuration.")

        return downloaders

    def _init_circuit_breakers(self) -> Dict[str, CircuitBreaker]:
        """
        为每个下载器初始化熔断器。

        Returns:
            下载器名称 -> 熔断器的映射
        """
        circuit_breakers: Dict[str, CircuitBreaker] = {}

        # 检查熔断器是否启用
        circuit_breaker_enabled = getattr(
            self.settings, "circuit_breaker_enabled", True
        )

        if not circuit_breaker_enabled:
            logger.info("Circuit breaker disabled by configuration")
            return circuit_breakers

        # 熔断器配置
        failure_threshold = getattr(
            self.settings, "circuit_breaker_threshold", 5
        )
        timeout = getattr(
            self.settings, "circuit_breaker_timeout", 1800
        )
        half_open_max_calls = getattr(
            self.settings, "circuit_breaker_half_open_calls", 3
        )

        for downloader in self.downloaders:
            circuit_breaker = CircuitBreaker(
                name=downloader.name,
                failure_threshold=failure_threshold,
                timeout=timeout,
                half_open_max_calls=half_open_max_calls,
            )
            circuit_breakers[downloader.name] = circuit_breaker

        logger.info(
            f"Circuit breakers initialized: "
            f"threshold={failure_threshold}, timeout={timeout}s"
        )

        return circuit_breakers

    def _merge_metadata(
        self,
        base: VideoMetadata,
        downloader_metadata: VideoMetadata,
    ) -> VideoMetadata:
        """
        合并两个元数据对象。

        策略：优先使用下载器返回的元数据（如果有值），否则使用基础元数据补充。
        这样可以确保即使下载器（如 CDP）只返回部分字段，也能获得完整的视频信息。

        Args:
            base: 基础元数据（通过 metadata_priority 获取的完整元数据）
            downloader_metadata: 下载器返回的元数据（可能不完整）

        Returns:
            合并后的完整元数据
        """
        return VideoMetadata(
            video_id=downloader_metadata.video_id,
            title=downloader_metadata.title or base.title,
            author=downloader_metadata.author or base.author,
            channel_id=downloader_metadata.channel_id or base.channel_id,
            duration=downloader_metadata.duration or base.duration,
            description=downloader_metadata.description or base.description,
            upload_date=downloader_metadata.upload_date or base.upload_date,
            view_count=downloader_metadata.view_count or base.view_count,
            thumbnail=downloader_metadata.thumbnail or base.thumbnail,
            source_downloader=(
                f"{downloader_metadata.source_downloader}+{base.source_downloader}"
                if downloader_metadata.source_downloader and base.source_downloader
                else downloader_metadata.source_downloader or base.source_downloader
            ),
        )

    def _get_prioritized_downloaders(
        self,
        include_audio: bool,
        include_transcript: bool,
    ) -> List[BaseDownloader]:
        """
        根据下载场景选择优先级配置。

        Args:
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            按优先级排序的下载器列表

        Raises:
            ValueError: 如果场景无效（audio 和 transcript 都为 False）
        """
        # 场景判断
        if include_audio and include_transcript:
            # 音频+字幕：使用音频优先级（大文件，高风控）
            priority_str = self.settings.audio_download_priority
            scenario = "audio+transcript"
        elif include_audio:
            # 仅音频：使用音频优先级
            priority_str = self.settings.audio_download_priority
            scenario = "audio-only"
        elif include_transcript:
            # 仅字幕：使用字幕优先级（轻量级，低风控）
            priority_str = self.settings.transcript_only_priority
            scenario = "transcript-only"
        else:
            raise ValueError("Invalid scenario: both include_audio and include_transcript are False")

        logger.info(
            f"[Scenario: {scenario}] Using priority config: {priority_str}"
        )

        # 解析优先级配置，构建下载器名称列表
        priority_names = [name.strip() for name in priority_str.split(",") if name.strip()]

        # 按优先级顺序构建下载器列表
        # 注意：只包含已初始化且可用的下载器
        prioritized: List[BaseDownloader] = []
        downloader_map = {d.name: d for d in self.downloaders}

        for name in priority_names:
            if name in downloader_map:
                prioritized.append(downloader_map[name])
            else:
                logger.debug(
                    f"Downloader '{name}' in priority config but not available (not initialized or unavailable)"
                )

        if not prioritized:
            logger.warning(
                f"No available downloaders for scenario '{scenario}' with priority '{priority_str}', "
                f"falling back to all available downloaders"
            )
            prioritized = [d for d in self.downloaders if d.supports_resource_download]

        logger.debug(
            f"[Scenario: {scenario}] Prioritized downloaders: {[d.name for d in prioritized]}"
        )

        return prioritized

    async def download_with_fallback(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        使用降级策略下载。

        按场景化优先级顺序尝试所有下载器，遇到熔断器开启时跳过。

        场景化优先级策略：
        - 音频下载：使用 AUDIO_DOWNLOAD_PRIORITY（如 "cdp,ytdlp,tikhub"）
        - 字幕下载：使用 TRANSCRIPT_ONLY_PRIORITY（如 "tikhub,ytdlp"）
        - 音频+字幕：使用 AUDIO_DOWNLOAD_PRIORITY

        元数据获取策略：
        - 在下载资源前，先使用 METADATA_PRIORITY 获取完整元数据
        - 如果下载器返回的元数据不完整（如 CDP 只返回 title），用预先获取的元数据补充
        - 确保最终返回的结果包含完整的视频信息（title、author、description 等）

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult 包含下载结果

        Raises:
            AllDownloadersFailed: 所有下载器都失败
            DownloadCancelledError: 下载被取消
        """
        errors: List[str] = []
        last_error: Optional[Exception] = None

        # 1. 先获取完整的视频元数据（使用 metadata_priority）
        # 这样确保即使使用仅支持音频下载的下载器（如 CDP），也能获得完整的视频信息
        logger.debug(f"Fetching metadata before download for {video_id}")
        metadata_dict = await self.get_metadata(video_url, video_id)

        if metadata_dict:
            logger.info(
                f"✓ Metadata fetched before download: {video_id} "
                f"(title: {metadata_dict.get('title', 'N/A')[:50]})"
            )
            # 转换为 VideoMetadata 对象
            base_metadata = VideoMetadata(
                video_id=video_id,
                title=metadata_dict.get("title"),
                author=metadata_dict.get("author"),
                channel_id=metadata_dict.get("channel_id"),
                duration=metadata_dict.get("duration"),
                description=metadata_dict.get("description"),
                upload_date=metadata_dict.get("upload_date"),
                view_count=metadata_dict.get("view_count"),
                thumbnail=metadata_dict.get("thumbnail"),
                source_downloader="metadata_priority",
            )
        else:
            logger.warning(
                f"Failed to fetch metadata for {video_id}, "
                f"will rely on downloader's metadata"
            )
            base_metadata = None

        # 2. 根据场景获取优先级排序后的下载器列表
        prioritized_downloaders = self._get_prioritized_downloaders(include_audio, include_transcript)

        for downloader in prioritized_downloaders:
            # 跳过不支持资源下载的下载器（如 YouTube Data API v3）
            if not downloader.supports_resource_download:
                logger.debug(
                    f"Skipping {downloader.name} - does not support resource downloads "
                    f"(metadata-only downloader)"
                )
                continue

            circuit_breaker = self.circuit_breakers.get(downloader.name)

            try:
                logger.info(
                    f"Trying downloader: {downloader.name} "
                    f"(circuit: {circuit_breaker.state.value if circuit_breaker else 'disabled'})"
                )

                # 使用熔断器包装调用
                if circuit_breaker:
                    result = await circuit_breaker.call_async(
                        lambda: self._download_with_downloader(
                            downloader,
                            video_url,
                            video_id,
                            output_dir,
                            include_audio,
                            include_transcript,
                        )
                    )
                else:
                    result = await self._download_with_downloader(
                        downloader,
                        video_url,
                        video_id,
                        output_dir,
                        include_audio,
                        include_transcript,
                    )

                # 成功：记录统计
                self.stats.record_success(downloader.name)
                logger.info(
                    f"✓ Download succeeded with {downloader.name} "
                    f"(success rate: {self.stats.get_success_rate(downloader.name):.1%})"
                )

                # 补充元数据：如果预先获取了完整元数据，用它补充下载器返回的不完整元数据
                if base_metadata:
                    result.video_metadata = self._merge_metadata(
                        base=base_metadata,
                        downloader_metadata=result.video_metadata,
                    )
                    logger.debug(
                        f"Merged metadata: source={result.video_metadata.source_downloader}"
                    )

                return result

            except CircuitBreakerOpen as e:
                # 熔断器开启，跳过此下载器
                logger.warning(f"✗ {downloader.name} circuit breaker open: {e.message}")
                errors.append(f"{downloader.name}: Circuit breaker open")
                continue

            except DownloadCancelledError:
                # 下载取消，直接向上抛出，不继续尝试其他下载器
                logger.info("Download cancelled, stopping fallback")
                raise

            except DownloaderError as e:
                # 下载器错误（已经过内部重试）
                logger.warning(
                    f"✗ {downloader.name} failed after retries: {e.error_code.value} - {e.message}"
                )
                errors.append(f"{downloader.name}: {e.message}")
                last_error = e

                # 记录统计
                self.stats.record_failure(downloader.name)

                # 判断是否应该触发熔断器
                if circuit_breaker and downloader.should_trigger_circuit_breaker(e):
                    logger.debug(f"Error will count towards circuit breaker for {downloader.name}")
                    # 熔断器已经通过 call_async 记录了失败

                    # 检查是否是全局性错误（如 nsig 失败），需要立即熔断
                    from src.db.models import ErrorCode
                    if e.error_code == ErrorCode.CDP_NSIG_FAILED:
                        circuit_breaker.force_open(
                            reason=f"nsig/n challenge failed - yt-dlp update required"
                        )
                        logger.warning(
                            f"[{downloader.name}] Force opened circuit breaker due to nsig error"
                        )
                else:
                    logger.debug(f"Error will not trigger circuit breaker for {downloader.name}")

                # 检查是否应该停止降级（如 403 本地 IP 问题）
                if e.stop_fallback:
                    logger.error(
                        f"Stop fallback due to {e.http_status_code or 'critical'} error - "
                        f"other downloaders will also fail with this issue"
                    )
                    # 直接抛出异常，不再尝试其他下载器
                    raise AllDownloadersFailed(errors)

                # 继续尝试下一个下载器（降级）
                # 注意：此时已经过了下载器内部的重试（最多3次）
                logger.info(f"Falling back to next downloader...")
                continue

            except Exception as e:
                # 未预期的错误
                logger.error(f"✗ {downloader.name} unexpected error: {e}")
                errors.append(f"{downloader.name}: {e}")
                last_error = e

                # 记录统计
                self.stats.record_failure(downloader.name)

                # 继续尝试下一个下载器
                continue

        # 所有下载器都失败了
        logger.error("All downloaders failed")
        logger.error(f"Error summary:\n" + "\n".join(f"  - {e}" for e in errors))

        # 显示统计信息
        logger.info(f"Downloader stats: {self.stats.get_summary()}")

        raise AllDownloadersFailed(errors)

    def _get_max_retries_for_error(self, error: DownloaderError) -> int:
        """
        根据错误类型决定最大重试次数。

        策略：
        - 403 错误（风控）：不重试，避免加重风控
        - RATE_LIMITED 错误：不重试，应该直接降级到其他下载器
        - 其他网络错误：最多重试 2 次

        Args:
            error: 下载器错误

        Returns:
            最大重试次数（0 表示不重试）
        """
        from src.db.models import ErrorCode

        # 403 错误（风控问题），不重试
        if error.http_status_code == 403:
            logger.info(
                f"HTTP 403 error detected (rate limiting/access control), "
                f"will not retry to avoid triggering more restrictions"
            )
            return 0

        # RATE_LIMITED 错误码，不重试（应该降级）
        if error.error_code == ErrorCode.RATE_LIMITED:
            logger.info(
                f"Rate limit error detected, will not retry (should fallback to next downloader)"
            )
            return 0

        # POT_TOKEN_FAILED 错误，不重试（重试也不会成功）
        if error.error_code == ErrorCode.POT_TOKEN_FAILED:
            logger.info(
                f"PO Token failed, will not retry (should fallback to next downloader)"
            )
            return 0

        # 其他错误，允许最多重试 2 次
        return 2

    async def _download_with_downloader(
        self,
        downloader: BaseDownloader,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool,
        include_transcript: bool,
    ) -> DownloaderResult:
        """
        使用指定下载器下载，带有重试和指数退避。

        重试策略：
        - 403/风控错误：不重试（避免加重风控）
        - 其他网络错误：最多重试 2 次（总共 3 次尝试）
        - 指数退避：1s, 2s
        - 只对 should_retry() 返回 True 的错误重试
        - 适用于临时性网络问题（ConnectError, TimeoutException 等）

        Args:
            downloader: 下载器实例
            video_url: 视频 URL
            video_id: 视频 ID
            output_dir: 输出目录
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult

        Raises:
            DownloaderError: 下载失败（包括重试后仍失败）
        """
        # 默认最大重试次数
        default_max_retries = 2

        for attempt in range(default_max_retries + 1):  # 0, 1, 2
            try:
                result = await downloader.download_resources(
                    video_url=video_url,
                    video_id=video_id,
                    output_dir=output_dir,
                    include_audio=include_audio,
                    include_transcript=include_transcript,
                )

                # 成功，如果之前有重试，记录成功信息
                if attempt > 0:
                    logger.info(
                        f"[{downloader.name}] Succeeded after {attempt} retry(ies)"
                    )

                return result

            except DownloaderError as e:
                # 根据错误类型动态决定最大重试次数
                max_retries = self._get_max_retries_for_error(e)
                is_last_attempt = attempt == max_retries

                # 判断是否应该重试
                should_retry = downloader.should_retry(e)

                if not should_retry:
                    # 不可重试的错误（如 API 限流、认证失败），直接抛出
                    logger.debug(
                        f"[{downloader.name}] Error is not retryable, aborting"
                    )
                    raise

                if is_last_attempt:
                    # 已达到最大重试次数
                    logger.warning(
                        f"[{downloader.name}] Max retries ({max_retries}) reached, giving up"
                    )
                    raise

                # 计算退避时间（指数退避：1s, 2s）
                backoff_delay = 2 ** attempt  # 1, 2

                logger.warning(
                    f"[{downloader.name}] Attempt {attempt + 1}/{max_retries + 1} failed: "
                    f"{e.error_code.value} - {e.message}"
                    f"{f' (HTTP {e.http_status_code})' if e.http_status_code else ''}"
                )
                logger.info(
                    f"[{downloader.name}] Retrying in {backoff_delay}s... "
                    f"(retry {attempt + 1}/{max_retries})"
                )

                await asyncio.sleep(backoff_delay)

            except Exception as e:
                # 未预期的错误，不重试
                logger.error(
                    f"[{downloader.name}] Unexpected error (not retrying): "
                    f"{type(e).__name__}: {e}"
                )
                raise

    def get_circuit_breaker_states(self) -> Dict[str, dict]:
        """
        获取所有熔断器的状态。

        Returns:
            熔断器状态字典
        """
        states = {}
        for name, circuit_breaker in self.circuit_breakers.items():
            states[name] = circuit_breaker.get_state_summary()
        return states

    def get_stats_summary(self) -> Dict[str, dict]:
        """
        获取统计摘要。

        Returns:
            统计摘要字典
        """
        return self.stats.get_summary()

    async def get_metadata(
        self,
        video_url: str,
        video_id: str,
        priority: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[dict]:
        """
        获取视频元数据（仅元数据，不下载资源）。

        缓存策略：
        1. 优先从数据库读取（永久有效，除非手动删除）
        2. 数据库未命中，按优先级调用下载器
        3. 成功后写入数据库

        并发控制：
        - 使用视频级别的锁，防止同一视频重复 API 调用
        - 双重检查模式（获取锁后再次检查缓存）

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            priority: 自定义优先级（如 "ytdlp,tikhub"），默认使用 metadata_priority
            force_refresh: 强制刷新（跳过数据库缓存）

        Returns:
            视频元数据字典，失败返回 None

        Example:
            # 使用默认优先级
            metadata = await manager.get_metadata(url, video_id)

            # 自定义优先级
            metadata = await manager.get_metadata(url, video_id, priority="tikhub,ytdlp")

            # 强制刷新
            metadata = await manager.get_metadata(url, video_id, force_refresh=True)
        """
        # 获取或创建锁
        if video_id not in self._metadata_locks:
            self._metadata_locks[video_id] = asyncio.Lock()

        async with self._metadata_locks[video_id]:
            # 1. 检查数据库缓存（双重检查）
            if not force_refresh and self.db:
                try:
                    resource = await self.db.get_video_resource(video_id)
                    if resource and resource.video_info:
                        # 获取 title 用于日志显示（处理 VideoInfo 对象和字典两种情况）
                        title = resource.video_info.title if hasattr(resource.video_info, 'title') else resource.video_info.get('title', 'N/A')
                        logger.info(
                            f"✓ Metadata from database cache: {video_id} "
                            f"(title: {title[:50] if title else 'N/A'})"
                        )
                        # 统一返回字典格式
                        if isinstance(resource.video_info, str):
                            return json.loads(resource.video_info)
                        elif hasattr(resource.video_info, 'to_dict'):
                            return resource.video_info.to_dict()
                        else:
                            return resource.video_info
                except Exception as e:
                    logger.warning(f"Failed to read metadata from database: {e}")

            # 2. 数据库未命中，按优先级调用下载器
            effective_priority = priority or getattr(self.settings, "metadata_priority", "ytdlp,tikhub")
            downloader_names = [name.strip() for name in effective_priority.split(",")]

            logger.info(
                f"Metadata cache miss, fetching from API: {video_id} "
                f"(priority: {effective_priority})"
            )

            errors: List[str] = []

            for name in downloader_names:
                # 查找下载器
                downloader = next((d for d in self.downloaders if d.name == name), None)
                if not downloader:
                    logger.debug(f"Downloader {name} not available, skipping")
                    continue

                # 使用重试逻辑调用下载器
                metadata = await self._fetch_metadata_with_retry(
                    downloader, video_url, video_id, errors
                )

                if metadata:
                    # 写入数据库（永久保存）
                    if self.db:
                        try:
                            # 确保 metadata 是字典
                            metadata_dict = metadata if isinstance(metadata, dict) else metadata.__dict__
                            await self._save_metadata_to_db(video_id, metadata_dict)
                        except Exception as e:
                            logger.warning(f"Failed to save metadata to database: {e}")

                    return metadata

            # 所有下载器都失败
            logger.error(f"All downloaders failed to fetch metadata for {video_id}")
            if errors:
                logger.error(f"Errors:\n" + "\n".join(f"  - {e}" for e in errors))

            return None

    async def _fetch_metadata_with_retry(
        self,
        downloader: BaseDownloader,
        video_url: str,
        video_id: str,
        errors: List[str],
    ) -> Optional[dict]:
        """
        使用指定下载器获取元数据，带有重试和指数退避。

        重试策略：
        - 临时性网络错误（SSL、超时）：最多重试 2 次
        - API 限流、配额超限：不重试（直接降级）
        - 指数退避：1s, 2s

        Args:
            downloader: 下载器实例
            video_url: 视频 URL
            video_id: 视频 ID
            errors: 错误列表（用于记录失败信息）

        Returns:
            视频元数据字典，失败返回 None
        """
        # 默认最大重试次数
        max_retries = 2

        for attempt in range(max_retries + 1):  # 0, 1, 2
            try:
                logger.debug(
                    f"Trying downloader: {downloader.name}"
                    + (f" (attempt {attempt + 1}/{max_retries + 1})" if attempt > 0 else "")
                )

                metadata = await downloader.fetch_metadata(video_url, video_id)

                if metadata:
                    logger.info(
                        f"✓ Metadata fetched from {downloader.name}: {video_id} "
                        f"(title: {metadata.get('title', 'N/A')[:50]})"
                        + (f" after {attempt} retry(ies)" if attempt > 0 else "")
                    )
                    return metadata

            except DownloaderError as e:
                # 判断是否应该重试
                should_retry = downloader.should_retry(e)
                is_last_attempt = attempt == max_retries

                if not should_retry:
                    # 不可重试的错误（如 API 限流、配额超限），直接降级
                    logger.warning(
                        f"Failed to fetch metadata with {downloader.name}: "
                        f"{e.error_code.value} - {e.message} (not retryable, will fallback)"
                    )
                    errors.append(f"{downloader.name}: {e.message}")
                    return None

                if is_last_attempt:
                    # 已达到最大重试次数
                    logger.warning(
                        f"Failed to fetch metadata with {downloader.name} "
                        f"after {max_retries + 1} attempts: {e.error_code.value} - {e.message}"
                    )
                    errors.append(f"{downloader.name}: {e.message} (after {max_retries + 1} attempts)")
                    return None

                # 计算退避时间（指数退避：1s, 2s）
                backoff_delay = 2 ** attempt  # 1, 2

                logger.warning(
                    f"[{downloader.name}] Attempt {attempt + 1}/{max_retries + 1} failed: "
                    f"{e.error_code.value} - {e.message}, retrying in {backoff_delay}s..."
                )

                await asyncio.sleep(backoff_delay)

            except Exception as e:
                # 未预期的错误，记录并降级
                logger.warning(
                    f"Failed to fetch metadata with {downloader.name}: {e} (unexpected error)"
                )
                errors.append(f"{downloader.name}: {e}")
                return None

        return None

    async def _save_metadata_to_db(self, video_id: str, metadata: dict) -> None:
        """
        保存元数据到数据库。

        Args:
            video_id: 视频 ID
            metadata: 元数据字典
        """
        if not self.db:
            return

        try:
            # 将字典转换为 VideoInfo 对象
            from src.db.models import VideoInfo

            video_info = VideoInfo(
                title=metadata.get("title"),
                author=metadata.get("author"),
                channel_id=metadata.get("channel_id"),
                duration=metadata.get("duration"),
                description=metadata.get("description"),
                upload_date=metadata.get("upload_date"),
                view_count=metadata.get("view_count"),
                thumbnail=metadata.get("thumbnail"),
            )

            # 检查是否已存在
            existing = await self.db.get_video_resource(video_id)

            if existing:
                # 更新现有记录
                await self.db.update_video_resource(
                    video_id=video_id,
                    video_info=video_info,
                )
                logger.debug(f"Updated metadata in database: {video_id}")
            else:
                # 创建新记录
                from src.db.models import VideoResource

                resource = VideoResource(
                    video_id=video_id,
                    video_info=video_info,
                    has_native_transcript=False,  # 暂时未知
                )
                await self.db.create_video_resource(resource)
                logger.debug(f"Saved metadata to database: {video_id}")

        except Exception as e:
            logger.error(f"Failed to save metadata to database: {e}", exc_info=True)
            # 不抛出异常，元数据获取成功就行

    def cancel_all(self) -> None:
        """取消所有下载器的当前操作。"""
        for downloader in self.downloaders:
            if hasattr(downloader, "cancel"):
                downloader.cancel()

    def reset_cancel_all(self) -> None:
        """重置所有下载器的取消状态。"""
        for downloader in self.downloaders:
            if hasattr(downloader, "reset_cancel"):
                downloader.reset_cancel()

    async def close(self) -> None:
        """关闭所有下载器（释放资源）。"""
        for downloader in self.downloaders:
            if hasattr(downloader, "close"):
                await downloader.close()
