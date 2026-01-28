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
from src.downloaders.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata
from src.downloaders.tikhub_downloader import TikHubDownloader
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

        根据配置的优先级顺序初始化下载器。

        Returns:
            下载器列表
        """
        downloaders: List[BaseDownloader] = []

        # 从配置读取优先级顺序
        priority_str = getattr(
            self.settings, "downloader_priority", "ytdlp,tikhub"
        )
        priority_list = [name.strip() for name in priority_str.split(",")]

        logger.info(f"Downloader priority: {priority_list}")

        for name in priority_list:
            try:
                if name == "ytdlp":
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

        按优先级顺序尝试所有下载器，遇到熔断器开启时跳过。

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

        for downloader in self.downloaders:
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

                # 成功：记录统计并返回
                self.stats.record_success(downloader.name)
                logger.info(
                    f"✓ Download succeeded with {downloader.name} "
                    f"(success rate: {self.stats.get_success_rate(downloader.name):.1%})"
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
                        logger.info(
                            f"✓ Metadata from database cache: {video_id} "
                            f"(title: {resource.video_info.get('title', 'N/A')[:50]})"
                        )
                        return json.loads(resource.video_info) if isinstance(resource.video_info, str) else resource.video_info
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

                try:
                    # 调用下载器获取元数据
                    logger.debug(f"Trying downloader: {downloader.name}")
                    metadata = await downloader.fetch_metadata(video_url, video_id)

                    if metadata:
                        logger.info(
                            f"✓ Metadata fetched from {downloader.name}: {video_id} "
                            f"(title: {metadata.get('title', 'N/A')[:50]})"
                        )

                        # 3. 写入数据库（永久保存）
                        if self.db:
                            try:
                                # 确保 metadata 是字典
                                metadata_dict = metadata if isinstance(metadata, dict) else metadata.__dict__
                                await self._save_metadata_to_db(video_id, metadata_dict)
                            except Exception as e:
                                logger.warning(f"Failed to save metadata to database: {e}")

                        return metadata

                except Exception as e:
                    error_msg = f"{downloader.name}: {str(e)}"
                    errors.append(error_msg)
                    logger.warning(f"Failed to fetch metadata with {downloader.name}: {e}")
                    continue

            # 所有下载器都失败
            logger.error(f"All downloaders failed to fetch metadata for {video_id}")
            if errors:
                logger.error(f"Errors:\n" + "\n".join(f"  - {e}" for e in errors))

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
            # 检查是否已存在
            existing = await self.db.get_video_resource(video_id)

            if existing:
                # 更新现有记录
                await self.db.update_video_resource(
                    video_id=video_id,
                    video_info=metadata,
                )
                logger.debug(f"Updated metadata in database: {video_id}")
            else:
                # 创建新记录
                from src.db.models import VideoResource
                resource = VideoResource(
                    video_id=video_id,
                    video_info=metadata,
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
