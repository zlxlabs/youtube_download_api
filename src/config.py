"""
Configuration management module.

Uses pydantic-settings to load and validate configuration from environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", str(Path(__file__).parent.parent / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ============ Required Configuration ============
    api_key: str = Field(..., description="API key for authentication")
    wecom_webhook_url: str = Field(
        default="", description="WeCom webhook URL for notifications"
    )

    # ============ Service Configuration ============
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8011, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")
    base_url: str = Field(
        default="http://localhost:8011",
        description="Base URL for file download links (e.g., https://your-domain.com)",
    )

    # ============ PO Token Service ============
    pot_provider_type: Literal["rust", "nodejs"] = Field(
        default="rust",
        description="PO Token Provider implementation: 'rust' (recommended, lighter) or 'nodejs' (original brainicism)",
    )
    pot_server_url: str = Field(
        default="http://pot-provider:4416",
        description="PO Token provider server URL",
    )

    # ============ Proxy Configuration ============
    http_proxy: Optional[str] = Field(default=None, description="HTTP proxy URL")
    https_proxy: Optional[str] = Field(default=None, description="HTTPS proxy URL")

    # ============ Download Configuration ============
    # TODO: 多 Worker 并发下载功能预留
    # 当前系统只启动单个 DownloadWorker，此配置暂未生效
    # 未来实现时需要在 main.py 中根据此值启动多个 worker
    # 注意：增加并发可能会提高 YouTube 风控风险
    download_concurrency: int = Field(
        default=1, ge=1, le=5, description="Number of concurrent downloads (reserved, not yet implemented)"
    )

    # ============ Task Interval Configuration ============
    # 字幕任务间隔（轻量级，低风控）
    transcript_interval_min: int = Field(
        default=20, ge=5, description="Minimum interval between transcript-only tasks (seconds)"
    )
    transcript_interval_max: int = Field(
        default=40, ge=10, description="Maximum interval between transcript-only tasks (seconds)"
    )

    # 音频/混合任务间隔（重量级，高风控）
    audio_interval_min: int = Field(
        default=60, ge=5, description="Minimum interval between audio/mixed tasks (seconds)"
    )
    audio_interval_max: int = Field(
        default=600, ge=10, description="Maximum interval between audio/mixed tasks (seconds)"
    )

    # 向后兼容：保留旧配置名称，映射到音频间隔
    task_interval_min: int = Field(
        default=60, ge=5, description="Minimum interval between tasks (seconds) - deprecated, use audio_interval_min"
    )
    task_interval_max: int = Field(
        default=600, ge=10, description="Maximum interval between tasks (seconds) - deprecated, use audio_interval_max"
    )

    audio_quality: int = Field(
        default=128, ge=64, le=320, description="Audio bitrate (kbps)"
    )

    # 任务级超时（兜底保护）
    task_timeout: int = Field(
        default=300, ge=60, le=3600, description="Task-level timeout in seconds (safety net)"
    )

    # ============ Storage Configuration ============
    data_dir: Path = Field(default=Path("./data"), description="Data storage directory")
    file_retention_days: int = Field(
        default=60, ge=1, description="File retention period (days)"
    )

    # ============ Timezone ============
    tz: str = Field(default="Asia/Shanghai", description="Timezone")

    # ============ TikHub API Configuration ============
    tikhub_api_key: Optional[str] = Field(
        default=None, description="TikHub API key for subtitle fetching"
    )

    # ============ YouTube Data API v3 Configuration ============
    youtube_data_api_key: Optional[str] = Field(
        default=None, description="YouTube Data API v3 key for metadata fetching"
    )

    # ============ CDP Downloader Configuration ============
    # 基础配置
    cdp_enabled: bool = Field(
        default=False,
        description="Enable CDP downloader (requires external Chrome)",
    )

    cdp_urls: str = Field(
        default="http://127.0.0.1:9222",
        description="CDP endpoint URLs (comma-separated, supports multiple instances for failover)",
    )

    cdp_timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="CDP connection timeout in seconds",
    )

    cdp_failover_strategy: str = Field(
        default="sequential",
        description="Failover strategy: sequential (ordered) or random",
    )

    # 功能开关
    cdp_enable_pot_token: bool = Field(
        default=False,
        description="Enable poToken support for CDP downloader (optional)",
    )

    cdp_use_curl_cffi: bool = Field(
        default=True,
        description="Use curl_cffi for TLS fingerprinting",
    )

    # 健康检查
    cdp_health_check_interval: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="CDP health check interval in seconds",
    )

    cdp_connection_retry: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Number of CDP connection retries",
    )

    # CDP 专用熔断器配置
    cdp_circuit_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="CDP circuit breaker: consecutive failure threshold",
    )

    cdp_circuit_timeout: int = Field(
        default=1800,
        ge=300,
        le=7200,
        description="CDP circuit breaker: timeout in seconds (default: 30 minutes)",
    )

    cdp_circuit_half_open_success: int = Field(
        default=2,
        ge=1,
        le=5,
        description="CDP circuit breaker: success threshold in half-open state",
    )

    # 企微通知频率限制
    cdp_notify_cooldown: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="CDP connection failure notification cooldown in seconds",
    )

    # 连接池配置（预留）
    cdp_max_connections: int = Field(
        default=1,
        ge=1,
        le=10,
        description="CDP max connections (currently only 1 is supported)",
    )

    # 转码配置
    cdp_transcode_to_m4a: bool = Field(
        default=False,
        description="Transcode downloaded audio to m4a format (default: False, keep original format to save time)",
    )

    # 分片下载配置
    cdp_enable_multipart: bool = Field(
        default=False,
        description="Enable multipart download for large files (experimental)",
    )
    cdp_multipart_chunks: int = Field(
        default=6,
        ge=2,
        le=16,
        description="Number of chunks for multipart download (recommended: 4-8)",
    )
    cdp_multipart_min_size: int = Field(
        default=10 * 1024 * 1024,  # 10MB
        ge=1 * 1024 * 1024,
        description="Minimum file size to enable multipart download (bytes)",
    )

    # CDP 人类行为模拟配置
    cdp_human_behavior_enabled: bool = Field(
        default=True,
        description="Enable CDP human behavior simulation to reduce YouTube bot detection risk",
    )

    cdp_quick_mode: bool = Field(
        default=False,
        description="Quick mode: skip human behavior simulation (for testing)",
    )

    cdp_watch_duration_min: int = Field(
        default=20,
        ge=5,
        le=120,
        description="Minimum video watch duration in seconds (human behavior simulation)",
    )
    cdp_watch_duration_max: int = Field(
        default=40,
        ge=10,
        le=180,
        description="Maximum video watch duration in seconds (human behavior simulation)",
    )

    cdp_page_alive_min: int = Field(
        default=30,
        ge=10,
        le=300,
        description="Minimum page alive duration before closing (seconds)",
    )
    cdp_page_alive_max: int = Field(
        default=60,
        ge=20,
        le=600,
        description="Maximum page alive duration before closing (seconds)",
    )

    cdp_scroll_probability: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="[DEPRECATED] Probability of scrolling the page (0.0-1.0) - now handled internally with realistic behavior layering",
    )
    cdp_pause_probability: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="[DEPRECATED] Probability of pausing/resuming video (0.0-1.0) - video is now always paused after watching (for the last page)",
    )

    # CDP 视频播放时长控制（基于视频总时长的智能计算）
    cdp_min_play_duration: float = Field(
        default=30.0,
        ge=0.0,
        description="Minimum video play duration in seconds (avoid short videos playing too briefly)",
    )
    cdp_max_play_duration: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="Maximum video play duration in seconds (default: 10 minutes)",
    )
    cdp_play_ratio_min: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum play ratio of video duration (default: 20%)",
    )
    cdp_play_ratio_max: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Maximum play ratio of video duration (default: 40%)",
    )

    @field_validator("cdp_watch_duration_max")
    @classmethod
    def validate_cdp_watch_duration_max(cls, v: int, info) -> int:
        """Ensure cdp_watch_duration_max >= cdp_watch_duration_min."""
        min_val = info.data.get("cdp_watch_duration_min", 20)
        if v < min_val:
            raise ValueError(
                f"cdp_watch_duration_max ({v}) must be >= cdp_watch_duration_min ({min_val})"
            )
        return v

    @field_validator("cdp_page_alive_max")
    @classmethod
    def validate_cdp_page_alive_max(cls, v: int, info) -> int:
        """Ensure cdp_page_alive_max >= cdp_page_alive_min."""
        min_val = info.data.get("cdp_page_alive_min", 30)
        if v < min_val:
            raise ValueError(
                f"cdp_page_alive_max ({v}) must be >= cdp_page_alive_min ({min_val})"
            )
        return v

    @field_validator("cdp_play_ratio_max")
    @classmethod
    def validate_cdp_play_ratio_max(cls, v: float, info) -> float:
        """Ensure cdp_play_ratio_max >= cdp_play_ratio_min."""
        min_val = info.data.get("cdp_play_ratio_min", 0.2)
        if v < min_val:
            raise ValueError(
                f"cdp_play_ratio_max ({v}) must be >= cdp_play_ratio_min ({min_val})"
            )
        return v

    @property
    def cdp_url_list(self) -> list[str]:
        """Parse CDP URL list from comma-separated string."""
        return [url.strip() for url in self.cdp_urls.split(",") if url.strip()]

    @property
    def cdp_connection_pool_enabled(self) -> bool:
        """Check if CDP connection pool is enabled (reserved)."""
        return self.cdp_max_connections > 1

    # ============ Downloader Configuration ============
    downloader_priority: str = Field(
        default="ytdlp,tikhub",
        description="[DEPRECATED] Use metadata_priority, transcript_only_priority, audio_download_priority instead",
    )

    # 元数据获取优先级（优先免费方案）
    metadata_priority: str = Field(
        default="ytdlp,tikhub",
        description="Metadata fetching priority (comma-separated, e.g., 'ytdlp,tikhub')",
    )

    # 仅字幕下载优先级（TikHub 更稳定）
    transcript_only_priority: str = Field(
        default="tikhub,ytdlp",
        description="Transcript-only download priority (comma-separated, e.g., 'tikhub,ytdlp')",
    )

    # 音频下载优先级（CDP 优先，然后降级到免费方案）
    audio_download_priority: str = Field(
        default="cdp,ytdlp,tikhub",
        description="Audio download priority (comma-separated, e.g., 'cdp,ytdlp,tikhub')",
    )

    # TikHub API 响应缓存时长
    tikhub_cache_ttl_hours: int = Field(
        default=3,
        ge=1,
        le=12,
        description="TikHub API response cache TTL in hours (1-12)",
    )

    # ============ Circuit Breaker Configuration ============
    circuit_breaker_enabled: bool = Field(
        default=True,
        description="Enable circuit breaker for downloaders",
    )
    circuit_breaker_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive failures to trigger circuit breaker",
    )
    circuit_breaker_timeout: int = Field(
        default=1800,
        ge=60,
        description="Circuit breaker timeout in seconds (default: 30 minutes)",
    )
    circuit_breaker_half_open_calls: int = Field(
        default=3,
        ge=1,
        description="Maximum calls allowed in half-open state",
    )

    # ============ WeCom Content Moderation ============
    wecom_moderation_enabled: bool = Field(
        default=False, description="Enable content moderation for WeCom notifications"
    )
    wecom_moderation_urls: Optional[str] = Field(
        default=None,
        description="Comma-separated list of sensitive word URLs",
    )
    wecom_moderation_strategy: str = Field(
        default="pinyin_reverse",
        description="Moderation strategy: block, replace, or pinyin_reverse",
    )

    # ============ Optional Configuration ============
    cookie_file: Optional[str] = Field(
        default=None, description="Path to cookie file for age-restricted videos"
    )
    dry_run: bool = Field(
        default=False, description="Dry run mode (skip actual downloads)"
    )

    # ============ Manual Upload Configuration ============
    manual_upload_enabled: bool = Field(
        default=True, description="Enable manual upload feature"
    )
    manual_upload_max_size_mb: int = Field(
        default=500, ge=1, description="Maximum upload file size (MB)"
    )
    manual_upload_allowed_video_formats: str = Field(
        default=".mp4,.webm,.mkv,.avi,.mov",
        description="Allowed video formats (comma-separated)",
    )
    manual_upload_allowed_audio_formats: str = Field(
        default=".m4a,.mp3,.aac,.opus,.wav,.flac,.ogg",
        description="Allowed audio formats (comma-separated)",
    )

    @field_validator("wecom_moderation_strategy")
    @classmethod
    def validate_moderation_strategy(cls, v: str) -> str:
        """Validate moderation strategy is one of the allowed values."""
        allowed = {"block", "replace", "pinyin_reverse"}
        if v.lower() not in allowed:
            raise ValueError(
                f"wecom_moderation_strategy must be one of {allowed}, got '{v}'"
            )
        return v.lower()

    def get_moderation_url_list(self) -> list[str]:
        """Parse moderation URLs from comma-separated string."""
        if not self.wecom_moderation_urls:
            return []
        return [url.strip() for url in self.wecom_moderation_urls.split(",") if url.strip()]

    @field_validator("data_dir", mode="before")
    @classmethod
    def validate_data_dir(cls, v: str | Path) -> Path:
        """Ensure data_dir is a Path object."""
        return Path(v) if isinstance(v, str) else v

    @field_validator("task_interval_max")
    @classmethod
    def validate_interval_max(cls, v: int, info) -> int:
        """Ensure task_interval_max >= task_interval_min."""
        min_val = info.data.get("task_interval_min", 30)
        if v < min_val:
            raise ValueError(
                f"task_interval_max ({v}) must be >= task_interval_min ({min_val})"
            )
        return v

    @field_validator("transcript_interval_max")
    @classmethod
    def validate_transcript_interval_max(cls, v: int, info) -> int:
        """Ensure transcript_interval_max >= transcript_interval_min."""
        min_val = info.data.get("transcript_interval_min", 20)
        if v < min_val:
            raise ValueError(
                f"transcript_interval_max ({v}) must be >= transcript_interval_min ({min_val})"
            )
        return v

    @field_validator("audio_interval_max")
    @classmethod
    def validate_audio_interval_max(cls, v: int, info) -> int:
        """Ensure audio_interval_max >= audio_interval_min."""
        min_val = info.data.get("audio_interval_min", 60)
        if v < min_val:
            raise ValueError(
                f"audio_interval_max ({v}) must be >= audio_interval_min ({min_val})"
            )
        return v

    @property
    def audio_dir(self) -> Path:
        """Directory for audio files."""
        return self.data_dir / "files" / "audio"

    @property
    def transcript_dir(self) -> Path:
        """Directory for transcript files."""
        return self.data_dir / "files" / "transcript"

    @property
    def db_path(self) -> Path:
        """Path to SQLite database file."""
        return self.data_dir / "db.sqlite"

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are only loaded once.

    Returns:
        Settings: Application settings instance.
    """
    return Settings()
