"""
Notification service module.

Handles sending notifications to WeCom (Enterprise WeChat) webhook.
"""

import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from src.config import Settings
from src.db.models import ErrorCode, Task, VideoInfo

# 这些错误是"预期内"的视频问题，不需要 @ 所有人
# 只有系统级错误才需要紧急通知
EXPECTED_VIDEO_ERRORS = {
    ErrorCode.VIDEO_UNAVAILABLE,  # 视频不存在/已删除
    ErrorCode.VIDEO_PRIVATE,  # 私密视频
    ErrorCode.VIDEO_REGION_BLOCKED,  # 地区限制
    ErrorCode.VIDEO_AGE_RESTRICTED,  # 年龄限制
    ErrorCode.VIDEO_LIVE_STREAM,  # 直播流（未开始的直播等）
}

# 各错误码对应的建议操作
ERROR_SUGGESTIONS: dict[ErrorCode, str] = {
    # 可直接重试
    ErrorCode.CDP_DOWNLOAD_403: "CDN 节点偶发限流，**直接重试**通常可解决",
    ErrorCode.CDP_DOWNLOAD_TIMEOUT: "下载超时，**直接重试**",
    ErrorCode.CDP_DOWNLOAD_FAILED: "下载失败，**直接重试**；若持续失败请检查服务日志",
    ErrorCode.NETWORK_ERROR: "网络异常，**直接重试**；若持续失败请检查代理连通性",
    ErrorCode.TASK_TIMEOUT: "任务超时，**直接重试**",
    ErrorCode.RATE_LIMITED: "请求频率过高，等待几分钟后**重试**",
    # 视频本身问题，无法解决
    ErrorCode.VIDEO_UNAVAILABLE: "视频已删除或不存在，无法下载",
    ErrorCode.VIDEO_PRIVATE: "私密视频，无法下载",
    ErrorCode.VIDEO_REGION_BLOCKED: "视频在当前地区受限，无法下载",
    ErrorCode.VIDEO_AGE_RESTRICTED: "年龄限制视频，需要有效的已登录 Cookie",
    ErrorCode.VIDEO_LIVE_STREAM: "直播视频暂不支持下载",
    # 系统/环境问题，需要人工介入
    ErrorCode.CDP_CONNECTION_FAILED: "Chrome 连接失败，检查 Chrome 是否正在运行",
    ErrorCode.CDP_CONNECTION_TIMEOUT: "Chrome 连接超时，检查 Chrome 状态和网络",
    ErrorCode.CDP_CHROME_NOT_READY: "Chrome 未就绪，稍等后重试或重启 Chrome",
    ErrorCode.CDP_NO_COOKIES: "Cookie 获取失败，检查 Chrome 是否已登录 YouTube",
    ErrorCode.CDP_YTDLP_FAILED: "yt-dlp 解析失败，可尝试**重试**；若持续失败请更新 yt-dlp",
    ErrorCode.CDP_NSIG_FAILED: "n-sig 解密失败，请更新 yt-dlp 版本",
    ErrorCode.CDP_SIZE_MISMATCH: "文件完整性校验失败，**直接重试**",
    ErrorCode.CDP_TRANSCODE_FAILED: "音频转码失败，检查 ffmpeg 是否正常",
}
from src.utils.helpers import format_duration, format_timedelta
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.db.database import Database
    from src.downloaders.manager import DownloaderManager

# Try to import wecom_notifier, but make it optional
try:
    from wecom_notifier import WeComNotifier

    WECOM_AVAILABLE = True
except ImportError:
    WECOM_AVAILABLE = False
    logger.warning("wecom-notifier not installed, notifications will be disabled")


class NotificationService:
    """
    Service for sending WeCom webhook notifications.

    Sends notifications for startup, task completion, and failures.
    """

    def __init__(
        self,
        settings: Settings,
        db: Optional["Database"] = None,
        downloader_manager: Optional["DownloaderManager"] = None,
    ):
        """
        Initialize notification service.

        Args:
            settings: Application settings.
            db: Database instance for fetching video info.
            downloader_manager: Downloader manager for stats (optional).
        """
        self.settings = settings
        self.db = db
        self.downloader_manager = downloader_manager
        self.webhook_url = settings.wecom_webhook_url
        self.enabled = bool(settings.wecom_webhook_url) and WECOM_AVAILABLE

        if WECOM_AVAILABLE and self.enabled:
            self.notifier = self._create_notifier()
        else:
            self.notifier = None

        if not self.enabled:
            logger.info("WeCom notifications disabled")

    def _create_notifier(self) -> "WeComNotifier":
        """
        Create WeComNotifier with optional content moderation.

        Returns:
            Configured WeComNotifier instance.
        """
        if not self.settings.wecom_moderation_enabled:
            logger.info("WeCom content moderation disabled")
            return WeComNotifier()

        # Get moderation URL list
        moderation_urls = self.settings.get_moderation_url_list()
        if not moderation_urls:
            logger.warning(
                "Content moderation enabled but no URLs configured, "
                "falling back to no moderation"
            )
            return WeComNotifier()

        # Build moderation config
        moderation_config = {
            "sensitive_word_urls": moderation_urls,
            "strategy": self.settings.wecom_moderation_strategy,
            "log_sensitive_messages": True,
            "cache_dir": str(self.settings.data_dir / ".wecom_cache"),
        }

        logger.info(
            f"WeCom content moderation enabled: "
            f"strategy={self.settings.wecom_moderation_strategy}, "
            f"urls={len(moderation_urls)}"
        )

        return WeComNotifier(
            enable_content_moderation=True,
            moderation_config=moderation_config,
        )

    def _format_local_time(self, dt: Optional[datetime]) -> str:
        """
        Format datetime to local timezone string.

        Args:
            dt: Datetime object (may be naive or aware).

        Returns:
            Formatted string in configured timezone.
        """
        if dt is None:
            return "N/A"

        try:
            tz: timezone | ZoneInfo = ZoneInfo(self.settings.tz)
        except Exception:
            # Fallback to UTC if timezone is invalid
            tz = timezone.utc

        # If datetime is naive, assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert to local timezone
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")

    def _get_local_now(self) -> str:
        """
        Get current time in local timezone.

        Returns:
            Formatted current time string.
        """
        try:
            tz: timezone | ZoneInfo = ZoneInfo(self.settings.tz)
        except Exception:
            tz = timezone.utc

        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    async def notify_startup(
        self,
        version: str,
        ip_ban_breaker=None,
    ) -> None:
        """
        发送系统启动通知（中文）。

        Args:
            version: 应用版本号
            ip_ban_breaker: IP 熔断器实例（可选）
        """
        if not self.enabled or not self.notifier:
            return

        try:
            hostname = socket.gethostname()
            ip = self._get_local_ip()

            # 构建启动通知内容
            content = f"""# 🚀 YouTube Audio API 已启动

## 📊 基础信息
🖥️ **主机**: {hostname} ({ip})
📦 **版本**: {version}
🕐 **启动时间**: {self._get_local_now()}
🌍 **时区**: {self.settings.tz}

## ⚙️ 核心配置

### 🚦 任务队列
> 并发数: {self.settings.download_concurrency}
> 字幕任务间隔: {self.settings.transcript_interval_min}-{self.settings.transcript_interval_max} 秒（轻量级）
> 音频任务间隔: {self.settings.audio_interval_min}-{self.settings.audio_interval_max} 秒（重量级）
> 文件保留: {self.settings.file_retention_days} 天

### 📥 下载器配置
> **元数据获取**: {self._format_priority(self.settings.metadata_priority)}
> **仅字幕**: {self._format_priority(self.settings.transcript_only_priority)}
> **音频下载**: {self._format_priority(self.settings.audio_download_priority)}
> **熔断器**: {self._format_circuit_breaker_status()}
> **TikHub 缓存**: {self.settings.tikhub_cache_ttl_hours} 小时
"""

            # CDP 下载器配置（条件显示）
            if self.settings.cdp_enabled:
                cdp_info = self._build_cdp_config_section()
                content += cdp_info

            # IP 熔断器状态
            if ip_ban_breaker:
                ip_ban_info = self._build_ip_ban_breaker_section(ip_ban_breaker)
                content += ip_ban_info

            # Cookie 配置（条件显示）
            cookie_info = self._build_cookie_section()
            if cookie_info:
                content += cookie_info

            # 人工上传配置（条件显示）
            if self.settings.manual_upload_enabled:
                upload_info = self._build_manual_upload_section()
                content += upload_info

            # 第三方服务（条件显示）
            third_party_info = self._build_third_party_section()
            if third_party_info:
                content += third_party_info

            # 企业微信审核（条件显示）
            if self.settings.wecom_moderation_enabled:
                moderation_info = self._build_moderation_section()
                content += moderation_info

            # 网络配置（仅开发环境显示）
            if self.settings.debug or self.settings.http_proxy or self.settings.https_proxy:
                network_info = self._build_network_section()
                if network_info:
                    content += network_info

            content += "\n---\n✨ 服务已就绪，可以接受请求！"

            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
            )
            logger.debug("Startup notification sent")

        except Exception as e:
            logger.error(f"Failed to send startup notification: {e}")

    def _format_priority(self, priority_str: str) -> str:
        """格式化下载器优先级字符串。"""
        parts = [p.strip().upper() for p in priority_str.split(",") if p.strip()]
        return " → ".join(parts)

    def _format_circuit_breaker_status(self) -> str:
        """格式化熔断器状态。"""
        if not self.settings.circuit_breaker_enabled:
            return "❌ 未启用"
        return (
            f"✅ 已启用（阈值={self.settings.circuit_breaker_threshold}, "
            f"超时={self.settings.circuit_breaker_timeout}秒）"
        )

    def _build_cdp_config_section(self) -> str:
        """构建 CDP 下载器配置段。"""
        # CDP URL 列表
        cdp_urls = self.settings.cdp_url_list
        cdp_urls_str = ", ".join(cdp_urls) if len(cdp_urls) <= 2 else f"{len(cdp_urls)} 个实例"

        # 分片下载状态
        multipart_status = "❌ 未启用"
        if self.settings.cdp_enable_multipart:
            min_size_mb = self.settings.cdp_multipart_min_size // (1024 * 1024)
            multipart_status = (
                f"✅ 已启用（{self.settings.cdp_multipart_chunks} chunks, "
                f"≥{min_size_mb} MB）"
            )

        # 人类行为模拟
        behavior_status = "❌ 未启用"
        if self.settings.cdp_human_behavior_enabled:
            if self.settings.cdp_quick_mode:
                behavior_status = "⚠️ 快速模式（跳过模拟）"
            else:
                behavior_status = (
                    f"✅ 已启用（观看 {self.settings.cdp_watch_duration_min}-"
                    f"{self.settings.cdp_watch_duration_max}秒）"
                )

        # poToken 状态
        pot_status = "✅ 已启用" if self.settings.cdp_enable_pot_token else "❌ 未启用"

        return f"""
### 🌐 CDP 下载器
> **状态**: ✅ 已启用
> **端点**: {cdp_urls_str}
> **故障转移**: {self.settings.cdp_failover_strategy}
> **TLS 指纹**: {'✅ 已启用（curl_cffi）' if self.settings.cdp_use_curl_cffi else '❌ 未启用'}
> **分片下载**: {multipart_status}
> **人类行为模拟**: {behavior_status}
> **熔断器**: 阈值={self.settings.cdp_circuit_failure_threshold}, 超时={self.settings.cdp_circuit_timeout}秒
> **poToken**: {pot_status}
"""

    def _build_ip_ban_breaker_section(self, ip_ban_breaker) -> str:
        """构建 IP 熔断器状态段。"""
        from src.core.ip_ban_models import IPBanLevel

        state = ip_ban_breaker.get_current_level()
        state_emoji = {
            IPBanLevel.NORMAL: "🟢",
            IPBanLevel.AUDIO_BANNED: "🟡",
            IPBanLevel.FULLY_BANNED: "🔴",
        }
        state_text = {
            IPBanLevel.NORMAL: "正常",
            IPBanLevel.AUDIO_BANNED: "音频熔断",
            IPBanLevel.FULLY_BANNED: "全局熔断",
        }

        emoji = state_emoji.get(state, "⚪")
        text = state_text.get(state, "未知")

        # 最近熔断时间
        last_ban_info = "无"
        if ip_ban_breaker.banned_at:
            last_ban_info = self._format_local_time(ip_ban_breaker.banned_at)

        return f"""
### 🛡️ IP 熔断器
> **状态**: {emoji} {text}
> **最近熔断**: {last_ban_info}
> **最小等待**: {ip_ban_breaker.MIN_WAIT_BEFORE_RETRY} 秒
> **重试间隔**: {ip_ban_breaker.MAX_RETRY_INTERVAL} 秒
"""

    def _build_cookie_section(self) -> str:
        """构建 Cookie 配置段。"""
        if not self.settings.cookie_file and not self.settings.pot_server_url:
            return ""

        cookie_status = "❌ 未配置"
        if self.settings.cookie_file:
            # 检查文件是否存在
            from pathlib import Path

            cookie_path = Path(self.settings.cookie_file)
            if cookie_path.exists():
                cookie_status = f"✅ 已配置（{self.settings.cookie_file}）"
            else:
                cookie_status = f"⚠️ 已配置但文件不存在（{self.settings.cookie_file}）"

        return f"""
### 🍪 Cookie 配置
> **Cookie 文件**: {cookie_status}
> **PO Token 服务**: {self.settings.pot_server_url}
"""

    def _build_manual_upload_section(self) -> str:
        """构建人工上传配置段。"""
        # 统计支持的格式数量
        video_formats = [
            f.strip()
            for f in self.settings.manual_upload_allowed_video_formats.split(",")
            if f.strip()
        ]
        audio_formats = [
            f.strip()
            for f in self.settings.manual_upload_allowed_audio_formats.split(",")
            if f.strip()
        ]

        return f"""
### 📤 人工上传
> **状态**: ✅ 已启用
> **最大文件**: {self.settings.manual_upload_max_size_mb} MB
> **支持格式**: 视频（{len(video_formats)} 种）、音频（{len(audio_formats)} 种）
"""

    def _build_third_party_section(self) -> str:
        """构建第三方服务配置段。"""
        services = []

        # YouTube Data API
        if self.settings.youtube_data_api_key:
            masked_key = self.settings.youtube_data_api_key[:6] + "***"
            services.append(f"> **YouTube Data API**: ✅ 已配置（{masked_key}）")

        # TikHub API
        if self.settings.tikhub_api_key:
            services.append("> **TikHub API**: ✅ 已配置")

        if not services:
            return ""

        return "\n### 🔑 第三方服务\n" + "\n".join(services) + "\n"

    def _build_moderation_section(self) -> str:
        """构建企业微信审核配置段。"""
        moderation_urls = self.settings.get_moderation_url_list()
        return f"""
### 🛡️ 企业微信审核
> **状态**: ✅ 已启用
> **策略**: {self.settings.wecom_moderation_strategy}
> **敏感词库**: {len(moderation_urls)} 个 URL
"""

    def _build_network_section(self) -> str:
        """构建网络配置段（仅开发环境）。"""
        items = []

        if self.settings.http_proxy:
            items.append(f"> **HTTP 代理**: {self.settings.http_proxy}")
        if self.settings.https_proxy:
            items.append(f"> **HTTPS 代理**: {self.settings.https_proxy}")
        if self.settings.base_url:
            items.append(f"> **Base URL**: {self.settings.base_url}")

        if not items:
            return ""

        return "\n### 🌐 网络配置\n" + "\n".join(items) + "\n"

    async def notify_shutdown(
        self,
        uptime_seconds: int,
        stats: dict | None = None,
    ) -> None:
        """
        发送系统关闭通知（中文）。

        Args:
            uptime_seconds: 运行时长（秒）
            stats: 统计信息（可选），包含 total_tasks, completed_tasks, failed_tasks
        """
        if not self.enabled or not self.notifier:
            return

        try:
            hostname = socket.gethostname()
            ip = self._get_local_ip()

            # 格式化运行时长
            uptime_str = self._format_uptime(uptime_seconds)

            content = f"""# 🛑 YouTube Audio API 已关闭

## 📊 基础信息
🖥️ **主机**: {hostname} ({ip})
🕐 **关闭时间**: {self._get_local_now()}
⏱️ **运行时长**: {uptime_str}
"""

            # 添加统计信息（如果有）
            if stats:
                total = stats.get("total_tasks", 0)
                completed = stats.get("completed_tasks", 0)
                failed = stats.get("failed_tasks", 0)
                success_rate = (completed / total * 100) if total > 0 else 0

                content += f"""
## 📈 运行统计
> **处理任务**: {total} 个
> **成功**: {completed} 个
> **失败**: {failed} 个
> **成功率**: {success_rate:.1f}%
"""

            content += "\n---\n👋 服务已安全关闭！"

            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
            )
            logger.debug("Shutdown notification sent")

        except Exception as e:
            logger.error(f"Failed to send shutdown notification: {e}")

    def _format_uptime(self, seconds: int) -> str:
        """
        格式化运行时长。

        Args:
            seconds: 秒数

        Returns:
            格式化的时长字符串（如：1 天 2 小时 30 分钟）
        """
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days} 天")
        if hours > 0:
            parts.append(f"{hours} 小时")
        if minutes > 0:
            parts.append(f"{minutes} 分钟")
        if secs > 0 or not parts:
            parts.append(f"{secs} 秒")

        return " ".join(parts)

    async def _get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        Get video info from database.

        Args:
            video_id: YouTube video ID.

        Returns:
            VideoInfo or None.
        """
        if not self.db:
            return None
        video_resource = await self.db.get_video_resource(video_id)
        return video_resource.video_info if video_resource else None

    async def notify_completed(
        self,
        task: Task,
        downloader: Optional[str] = None,
        transcript_fallback: bool = False,
    ) -> None:
        """
        Send task completion notification.

        Args:
            task: Completed task.
            downloader: Name of the downloader that succeeded (optional).
            transcript_fallback: Whether this was a transcript fallback (audio failed).
        """
        if not self.enabled or not self.notifier:
            return

        try:
            video_info = await self._get_video_info(task.video_id)
            title = video_info.title if video_info else "Unknown"
            author = video_info.author if video_info else "Unknown"
            duration = (
                format_duration(video_info.duration)
                if video_info and video_info.duration
                else "N/A"
            )

            # 获取视频描述，截断过长内容
            description = ""
            if video_info and video_info.description:
                desc = video_info.description
                max_len = 200
                if len(desc) > max_len:
                    description = desc[:max_len] + "..."
                else:
                    description = desc

            # 构建文件下载链接（带后缀）
            base_url = self.settings.base_url.rstrip("/")
            audio_url = "N/A"
            transcript_url = "无字幕"

            if task.audio_file_id and self.db:
                audio_file = await self.db.get_file(task.audio_file_id)
                if audio_file:
                    # Fallback to extension from filename if format field is empty
                    audio_ext = audio_file.format or Path(audio_file.filename).suffix.lstrip(".") or "m4a"
                    audio_url = f"{base_url}/api/v1/files/{task.audio_file_id}.{audio_ext}"

            if task.transcript_file_id and self.db:
                transcript_file = await self.db.get_file(task.transcript_file_id)
                if transcript_file:
                    transcript_ext = transcript_file.format or "srt"
                    transcript_url = f"{base_url}/api/v1/files/{task.transcript_file_id}.{transcript_ext}"

            # 格式化时间信息（转换为本地时区）
            created_time = self._format_local_time(task.created_at)
            started_time = self._format_local_time(task.started_at)
            wait_time = (
                format_timedelta(task.started_at - task.created_at)
                if task.created_at and task.started_at
                else "N/A"
            )

            # 构建下载器信息
            downloader_info = ""
            if downloader:
                downloader_info = f"📥 **Downloader**: {downloader.upper()}\n"

                # 添加统计信息
                if self.downloader_manager:
                    stats = self.downloader_manager.get_stats_summary().get(downloader, {})
                    if stats.get("total", 0) > 0:
                        success_rate = stats.get("success_rate", 0.0) * 100
                        downloader_info += f"📊 **Success Rate**: {success_rate:.1f}% ({stats.get('success', 0)}/{stats.get('total', 0)})\n"

            # 资源复用标识
            reuse_info = ""
            if task.reused_audio or task.reused_transcript:
                reuse_parts = []
                if task.reused_audio:
                    reuse_parts.append("音频")
                if task.reused_transcript:
                    reuse_parts.append("字幕")
                reuse_info = f"♻️ **Reused**: {', '.join(reuse_parts)}\n"

            # 字幕降级标识
            fallback_info = ""
            if transcript_fallback:
                fallback_info = f"⚠️ **Note**: Audio download failed, completed with transcript only\n"

            content = f"""# ✅ Download Completed

🎬 **Video**: {title}
👤 **Author**: {author}
⏱️ **Duration**: {duration}

🔗 **Video URL**: {task.video_url}

📝 **Description**:
> {description if description else "无描述"}

🎵 **Audio**: {audio_url}
📄 **Transcript**: {transcript_url if task.transcript_file_id else "无字幕"}

{downloader_info}{reuse_info}{fallback_info}📅 **Created**: {created_time}
▶️ **Started**: {started_time}
⏳ **Wait Time**: {wait_time}

🆔 **Task ID**: `{task.id}`
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
            )
            logger.debug(f"Completion notification sent for task {task.id}")

        except Exception as e:
            logger.error(f"Failed to send completion notification: {e}")

    async def notify_failed(
        self, task: Task, error: str, failed_downloaders: Optional[list[str]] = None
    ) -> None:
        """
        Send task failure notification.

        Args:
            task: Failed task.
            error: Error message.
            failed_downloaders: List of downloaders that failed (optional).
        """
        if not self.enabled or not self.notifier:
            return

        try:
            # 获取视频标题（如果有）
            video_info = await self._get_video_info(task.video_id)
            title = video_info.title if video_info else "Unknown"

            # 获取错误码（如果有）
            error_code = task.error_code.value if task.error_code else "UNKNOWN"

            # 判断是否需要 @ 所有人
            # 只有系统级错误才需要紧急通知，视频本身的问题不需要
            should_mention_all = task.error_code not in EXPECTED_VIDEO_ERRORS

            # 根据错误类型选择不同的 emoji 和标题
            if should_mention_all:
                title_emoji = "❌"
                title_text = "Download Failed"
            else:
                title_emoji = "⚠️"
                title_text = "Download Skipped"

            # 格式化时间信息（转换为本地时区）
            created_time = self._format_local_time(task.created_at)
            started_time = self._format_local_time(task.started_at)
            wait_time = (
                format_timedelta(task.started_at - task.created_at)
                if task.created_at and task.started_at
                else "N/A"
            )

            # 构建下载器失败信息
            downloader_info = ""
            if failed_downloaders:
                downloader_info = f"❌ **Failed Downloaders**: {', '.join(d.upper() for d in failed_downloaders)}\n"

            # 熔断器状态信息
            circuit_breaker_info = ""
            if self.downloader_manager and self.downloader_manager.circuit_breakers:
                cb_states = self.downloader_manager.get_circuit_breaker_states()
                open_circuits = [
                    name for name, state in cb_states.items() if state.get("state") == "open"
                ]
                if open_circuits:
                    circuit_breaker_info = f"🔌 **Circuit Open**: {', '.join(c.upper() for c in open_circuits)}\n"

            # 根据错误码获取建议操作
            suggestion = ERROR_SUGGESTIONS.get(task.error_code, "")
            suggestion_section = f"\n> {suggestion}\n" if suggestion else ""

            content = f"""# {title_emoji} {title_text}

🎬 **Video**: {title}
🔗 **Video URL**: {task.video_url}

💥 **Error Code**: `{error_code}`
📋 **Error Message**: {error}
{suggestion_section}
{downloader_info}{circuit_breaker_info}📅 **Created**: {created_time}
▶️ **Started**: {started_time}
⏳ **Wait Time**: {wait_time}

🔄 **Retry Count**: {task.retry_count}
🆔 **Task ID**: `{task.id}`
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=should_mention_all,
            )
            logger.debug(f"Failure notification sent for task {task.id}")

        except Exception as e:
            logger.error(f"Failed to send failure notification: {e}")

    async def notify_cookie_expired(self) -> None:
        """Send cookie expiration warning notification."""
        if not self.enabled or not self.notifier:
            return

        try:
            content = """# ⚠️ Cookie Expired Warning

🍪 YouTube cookie has expired. Some features may be limited:

> ❌ Age-restricted videos cannot be downloaded
> ❌ Member-only content cannot be downloaded

🔧 Please update the cookie file and restart the service.
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=True,
            )
            logger.warning("Cookie expiration notification sent")

        except Exception as e:
            logger.error(f"Failed to send cookie expiration notification: {e}")

    async def notify_disk_space_warning(self, free_mb: int) -> None:
        """
        Send low disk space warning notification.

        Args:
            free_mb: Free disk space in MB.
        """
        if not self.enabled or not self.notifier:
            return

        try:
            content = f"""# ⚠️ Low Disk Space Warning

💾 Available disk space is running low!

📉 **Free Space**: {free_mb} MB

🔧 Please clean up old files or expand storage.
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=True,
            )
            logger.warning(f"Disk space warning notification sent: {free_mb}MB free")

        except Exception as e:
            logger.error(f"Failed to send disk space notification: {e}")

    # ========== CDP 下载器通知 ==========

    async def notify_cdp_connection_failed(
        self,
        error: str,
        cdp_url: str,
    ) -> None:
        """
        发送 CDP 连接失败通知。

        Args:
            error: 错误信息
            cdp_url: CDP 地址
        """
        if not self.enabled or not self.notifier:
            return

        try:
            # 截断错误信息
            error_preview = error[:500] if len(error) > 500 else error

            content = f"""# ⚠️ CDP 下载器连接失败

**错误信息：**
```
{error_preview}
```

**CDP 地址：** {cdp_url}

**影响范围：**
- CDP 下载器暂时不可用
- 已自动降级到 ytdlp/tikhub
- 不影响任务正常执行

**建议操作：**
1. 检查 Chrome 是否正在运行
2. 确认启动命令：
   ```
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
     --remote-debugging-port=9222 \\
     --user-data-dir=/tmp/chrome-cdp
   ```
3. 检查网络连接和防火墙
4. 查看 Chrome 日志：chrome://inspect

**熔断状态：**
- 连续失败后将触发熔断器（30 分钟）
- 自动恢复后会继续尝试使用 CDP
"""

            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=False,  # 不 @ 所有人，降低打扰
            )
            logger.info(f"CDP connection failure notification sent: {cdp_url}")

        except Exception as e:
            logger.error(f"Failed to send CDP connection notification: {e}")

    async def notify_cdp_circuit_breaker_open(
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
        if not self.enabled or not self.notifier:
            return

        try:
            # 计算熔断时长
            import time

            open_duration_min = int((open_until_timestamp - time.time()) / 60)

            # 格式化恢复时间
            from datetime import datetime

            open_until_dt = datetime.fromtimestamp(open_until_timestamp)
            recovery_time = open_until_dt.strftime("%Y-%m-%d %H:%M:%S")

            content = f"""# 🚨 CDP 熔断器已打开

**触发原因：**
连续失败 {consecutive_failures} 次

**熔断时长：**
{open_duration_min} 分钟

**影响：**
- CDP 下载器暂停使用
- 所有任务使用 ytdlp/tikhub
- 熔断结束后自动恢复

**自动恢复时间：**
{recovery_time}

**人工干预（可选）：**
1. 检查并修复 Chrome 连接问题
2. 确认网络连接正常
3. 等待自动恢复即可（无需手动操作）
"""

            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=True,  # 熔断是重要事件，需要 @ 所有人
            )
            logger.warning(
                f"CDP circuit breaker open notification sent: "
                f"{consecutive_failures} failures, open until {recovery_time}"
            )

        except Exception as e:
            logger.error(f"Failed to send CDP circuit breaker notification: {e}")

    async def notify_cdp_recovered(self) -> None:
        """
        发送 CDP 恢复通知。
        """
        if not self.enabled or not self.notifier:
            return

        try:
            content = """# ✅ CDP 下载器已恢复

CDP 熔断器已恢复正常状态，可继续使用。

**状态：**
- 熔断器：已关闭
- 下载器：可用
- 优先级：已恢复为音频下载首选

**下一步：**
无需手动操作，系统已自动恢复正常。
"""

            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=False,
            )
            logger.info("CDP recovery notification sent")

        except Exception as e:
            logger.error(f"Failed to send CDP recovery notification: {e}")

    # ========== 辅助方法 ==========

    def _get_local_ip(self) -> str:
        """
        Get local IP address.

        Returns:
            Local IP address string.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip: str = s.getsockname()[0]  # type: ignore
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def send_ip_ban_notification(self, level: str, reason: str) -> None:
        """
        发送 IP 熔断通知。

        Args:
            level: 熔断级别 ("audio" 或 "full")
            reason: 熔断原因
        """
        if not self.enabled or not self.notifier:
            return

        try:
            if level == "audio":
                title = "⚠️ IP 状态变更 - 音频熔断"
                impact = "音频/混合任务暂停"
                available = "仅字幕任务正常"
                recovery = "预计 60 分钟后探测"
            else:
                title = "🔒 IP 状态变更 - 全局熔断"
                impact = "所有任务暂停"
                available = "无可用服务"
                recovery = "预计 90-120 分钟后探测"

            content = f"""# {title}

**级别**: {level}_banned
**影响**: {impact}
**可用**: {available}
**原因**: {reason}

⏰ **恢复**: {recovery}

系统将利用用户任务被动探测恢复状态。
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=True,
            )
            logger.info(f"IP ban notification sent: level={level}")

        except Exception as e:
            logger.error(f"Failed to send IP ban notification: {e}")

    async def send_ip_recovery_notification(self, message: str) -> None:
        """
        发送 IP 恢复通知。

        Args:
            message: 恢复消息
        """
        if not self.enabled or not self.notifier:
            return

        try:
            content = f"""# ✅ IP 状态恢复

{message}

服务已恢复正常运行！
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
                mention_all=True,
            )
            logger.info("IP recovery notification sent")

        except Exception as e:
            logger.error(f"Failed to send IP recovery notification: {e}")
