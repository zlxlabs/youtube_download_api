"""
Notification service module.

Handles sending notifications to WeCom (Enterprise WeChat) webhook.
"""

import socket
from datetime import datetime, timezone
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

    async def notify_startup(self, version: str) -> None:
        """
        Send system startup notification.

        Args:
            version: Application version.
        """
        if not self.enabled or not self.notifier:
            return

        try:
            hostname = socket.gethostname()
            ip = self._get_local_ip()

            # Build moderation status string
            if self.settings.wecom_moderation_enabled:
                moderation_urls = self.settings.get_moderation_url_list()
                moderation_status = (
                    f"Enabled ({self.settings.wecom_moderation_strategy}, "
                    f"{len(moderation_urls)} URLs)"
                )
            else:
                moderation_status = "Disabled"

            # Build downloader configuration string
            downloader_config = ""
            if self.downloader_manager:
                # 下载器优先级
                downloader_names = [d.name for d in self.downloader_manager.downloaders]
                if downloader_names:
                    downloader_config += f"> 📥 Downloaders: {' → '.join(downloader_names)}\n"

                # 熔断器配置
                if self.downloader_manager.circuit_breakers:
                    cb_threshold = getattr(self.settings, "circuit_breaker_threshold", 5)
                    cb_timeout = getattr(self.settings, "circuit_breaker_timeout", 1800)
                    downloader_config += f"> 🔌 Circuit Breaker: threshold={cb_threshold}, timeout={cb_timeout}s\n"
                else:
                    downloader_config += "> 🔌 Circuit Breaker: Disabled\n"

            content = f"""# 🚀 YouTube Audio API Started

🖥️ **Host**: {hostname} ({ip})
📦 **Version**: {version}
🕐 **Time**: {self._get_local_now()}

⚙️ **Configuration**:
> 📊 Concurrency: {self.settings.download_concurrency}
> ⏳ Task Interval: {self.settings.task_interval_min}-{self.settings.task_interval_max}s
> 🗂️ File Retention: {self.settings.file_retention_days} days
> 🔑 PO Token: {self.settings.pot_server_url}
> 🛡️ Content Moderation: {moderation_status}
{downloader_config}
✨ Service is ready to accept requests!
"""
            self.notifier.send_markdown(
                webhook_url=self.webhook_url,
                content=content,
            )
            logger.debug("Startup notification sent")

        except Exception as e:
            logger.error(f"Failed to send startup notification: {e}")

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
                    audio_ext = audio_file.format or "m4a"
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

            content = f"""# {title_emoji} {title_text}

🎬 **Video**: {title}
🔗 **Video URL**: {task.video_url}

💥 **Error Code**: `{error_code}`
📋 **Error Message**: {error}

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

    def _get_local_ip(self) -> str:
        """
        Get local IP address.

        Returns:
            Local IP address string.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
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
