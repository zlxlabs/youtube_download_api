"""
统一的下载器数据模型。

提供跨下载器的标准化数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from src.db.models import ErrorCode


class DownloaderType(str, Enum):
    """下载器类型枚举。"""

    CDP = "cdp"
    YTDLP = "ytdlp"
    TIKHUB = "tikhub"
    YOUTUBE_DATA_API = "youtube_data_api"


# 元数据/资源获取阶段的"内容级终态错误"：由视频自身客观状态导致、与具体下载器
# 实现/出口位置无关的错误码（视频不存在/私有/直播/年龄限制）——不管用哪个下载器、
# 从哪台机器发起请求，结果都一样，因此可以作为全局终态立即终止降级链。
#
# 各下载器的 fetch_metadata() 遇到这些错误时必须抛出 DownloaderError（而非吞掉
# 返回 None），供 DownloaderManager.get_metadata(raise_content_errors=True) 感知
# 并终止降级链——这是 TaskService precheck 422 拦截依赖的唯一信号源。
#
# 注意：VIDEO_REGION_BLOCKED（地区限制）不在这个集合里。地区限制是"下载器/出口
# 位置相关"的错误，不是视频的客观状态——本地部署的 ytdlp 被某个地区封锁，不代表
# 其他下载器（如远端服务 TikHub）也下载不了同一个视频。若把它当全局终态处理，
# 会出现"本地 ytdlp 探测到地区限制 -> 丢弃前面已经成功的 TikHub 结果 -> precheck
# 422 拒绝一个实际可下载的视频"这种误判（外部 review 第13轮问题2）。因此地区限制
# 不终止降级链、不触发 precheck 422，交给完整的下载器降级链（含远端下载器）自行
# 判定；只有 UNAVAILABLE/PRIVATE/LIVE/AGE_RESTRICTED 这类视频客观状态才算全局终态。
#
# 定义放在这个无下游依赖的叶子模块（而非 manager.py），是因为 manager.py 会反过来
# 导入各下载器实现类，若把这个常量放在 manager.py 再被下载器模块导入会形成循环导入。
# manager.py 从这里导入并保留同名属性，因此 `from src.downloaders.manager import
# CONTENT_LEVEL_ERROR_CODES` 的旧引用（测试等）不受影响。
CONTENT_LEVEL_ERROR_CODES = frozenset(
    {
        ErrorCode.VIDEO_UNAVAILABLE,
        ErrorCode.VIDEO_PRIVATE,
        ErrorCode.VIDEO_LIVE_STREAM,
        ErrorCode.VIDEO_AGE_RESTRICTED,
    }
)


@dataclass
class VideoMetadata:
    """
    统一的视频元数据模型。

    适配不同下载器返回的视频信息格式。
    """

    video_id: str
    title: Optional[str] = None
    author: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[int] = None  # 秒
    description: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    thumbnail: Optional[str] = None

    # 元数据：标识数据来源
    source_downloader: Optional[str] = None


@dataclass
class DownloaderResult:
    """
    统一的下载结果模型。

    包含下载的文件路径、视频元数据和执行信息。
    支持部分成功（例如：混合任务中音频失败但字幕成功）。
    """

    # 执行信息
    success: bool
    downloader: str  # 实际使用的下载器

    # 视频元数据
    video_metadata: VideoMetadata

    # 文件路径（临时目录中的路径）
    audio_path: Optional[Path] = None
    transcript_path: Optional[Path] = None

    # 字幕信息
    has_transcript: bool = False  # 视频是否有可用字幕

    # 部分成功支持
    partial_success: bool = False  # 是否是部分成功（请求多项但只成功部分）

    # 分项错误信息（部分失败时）
    audio_error: Optional[str] = None  # 音频下载失败原因
    audio_error_code: Optional[str] = None  # 音频错误码（ErrorCode.value）
    transcript_error: Optional[str] = None  # 字幕获取失败原因
    transcript_error_code: Optional[str] = None  # 字幕错误码

    # 失败详情（结构化数据，用于分析和 API 返回）
    failure_details: Optional[dict[str, Any]] = field(default_factory=lambda: None)

    # 错误信息（完全失败时）
    error: Optional[str] = None
