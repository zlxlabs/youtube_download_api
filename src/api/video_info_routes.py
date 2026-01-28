"""
视频元数据查询 API 路由。

提供快速查询视频元数据的端点，无需下载文件。
"""

import re
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import ApiKeyDep
from src.api.schemas import VideoInfoDetailResponse, VideoInfoResponse
from src.db.database import Database
from src.db.models import ErrorCode, VideoInfo
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.manager import DownloaderManager
from src.utils.logger import logger


# Router for video info API
router = APIRouter(prefix="/api/v1", tags=["Video Info"])


# ==================== Dependency Injection ====================

# 全局变量用于依赖注入（在 app 启动时设置）
_database: Optional[Database] = None
_downloader_manager: Optional[DownloaderManager] = None


def set_services(db: Database, downloader_manager: DownloaderManager) -> None:
    """
    设置服务实例（在 app 启动时调用）。

    Args:
        db: 数据库实例
        downloader_manager: 下载器管理器实例
    """
    global _database, _downloader_manager
    _database = db
    _downloader_manager = downloader_manager


def get_database() -> Database:
    """获取数据库实例"""
    if _database is None:
        raise RuntimeError("Database not initialized")
    return _database


def get_downloader_manager() -> DownloaderManager:
    """获取下载器管理器实例"""
    if _downloader_manager is None:
        raise RuntimeError("DownloaderManager not initialized")
    return _downloader_manager


DatabaseDep = Annotated[Database, Depends(get_database)]
DownloaderManagerDep = Annotated[DownloaderManager, Depends(get_downloader_manager)]


# ==================== Helper Functions ====================


def _is_valid_video_id(video_id: str) -> bool:
    """
    验证 YouTube 视频 ID 格式。

    Args:
        video_id: YouTube 视频 ID

    Returns:
        True 表示格式有效，False 表示无效
    """
    # YouTube 视频 ID 格式：11 位字母数字字符，可包含 - 和 _
    return bool(re.match(r'^[a-zA-Z0-9_-]{11}$', video_id))


def _build_video_info_response(video_info: VideoInfo) -> VideoInfoResponse:
    """
    从 VideoInfo 模型构建响应对象。

    Args:
        video_info: VideoInfo 数据模型

    Returns:
        VideoInfoResponse 响应对象
    """
    return VideoInfoResponse(
        title=video_info.title,
        author=video_info.author,
        channel_id=video_info.channel_id,
        duration=video_info.duration,
        description=video_info.description,
        upload_date=video_info.upload_date,
        view_count=video_info.view_count,
        thumbnail=video_info.thumbnail,
    )


# ==================== API Endpoints ====================


@router.get(
    "/videos/{video_id}/info",
    response_model=VideoInfoDetailResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"description": "Video not found"},
        503: {"description": "Service unavailable - unable to fetch metadata"},
    },
    summary="Get video metadata",
    description="Retrieve YouTube video metadata (title, author, duration, etc.) without downloading files. "
                "Uses database cache if available, otherwise fetches from YouTube Data API or fallback downloaders.",
)
async def get_video_info(
    video_id: str,
    _: ApiKeyDep,
    db: DatabaseDep,
    downloader_manager: DownloaderManagerDep,
) -> VideoInfoDetailResponse:
    """
    获取 YouTube 视频元数据（不下载文件）。

    流程：
    1. 验证 video_id 格式
    2. 检查数据库缓存（video_resources 表）
    3. 缓存命中 → 直接返回
    4. 缓存未命中 → 调用 DownloaderManager.get_metadata()
       - 优先使用 YouTube Data API（如果已配置 API Key）
       - 未配置或失败时自动降级到 ytdlp/tikhub
    5. 保存元数据到数据库（永久缓存）
    6. 返回结果

    Args:
        video_id: YouTube 视频 ID（11 位字符）
        _: API Key 鉴权依赖
        db: 数据库实例
        downloader_manager: 下载器管理器实例

    Returns:
        VideoInfoDetailResponse: 包含视频元数据的响应对象

    Raises:
        HTTPException(400): video_id 格式无效
        HTTPException(404): 视频不存在或已删除
        HTTPException(500): 获取元数据失败
        HTTPException(503): 所有下载器不可用
    """
    # 1. 验证 video_id 格式
    if not _is_valid_video_id(video_id):
        logger.warning(f"Invalid video ID format: {video_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid YouTube video ID format: {video_id}",
        )

    logger.info(f"Fetching video info: {video_id}")

    # 2. 检查数据库缓存
    video_resource = await db.get_video_resource(video_id)

    if video_resource and video_resource.video_info:
        # 3. 缓存命中，直接返回
        logger.info(f"Video metadata cache hit: {video_id}")

        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=_build_video_info_response(video_resource.video_info),
            cached=True,
            metadata_source="cached",
            fetched_at=video_resource.updated_at,
        )

    # 4. 缓存未命中，获取元数据
    logger.info(f"Video metadata cache miss: {video_id}, fetching from API")

    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        # 调用 DownloaderManager（自动处理降级）
        # 优先级由 METADATA_PRIORITY 配置决定（如 youtube_data_api,ytdlp,tikhub）
        metadata = await downloader_manager.get_metadata(
            video_url=video_url,
            video_id=video_id,
        )

        if not metadata:
            # 获取元数据失败（所有下载器都失败了）
            raise AllDownloadersFailed(
                errors=["All downloaders failed to fetch metadata"],
            )

        logger.info(
            f"Metadata fetched successfully: {video_id} "
            f"(title: {metadata.get('title', 'N/A')[:50]})"
        )

        # 5. 保存到数据库（永久缓存）
        # metadata 是字典，需要转换为 VideoInfo 对象
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

        await db.update_video_resource(
            video_id=video_id,
            video_info=video_info,
            has_native_transcript=None,  # 元数据模式不检查字幕
        )

        logger.debug(f"Metadata saved to database: {video_id}")

        # 6. 返回结果
        # metadata_source: 由于 DownloaderManager 返回字典不包含来源信息，
        # 暂时使用 "api" 标识（来自下载器 API）
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=VideoInfoResponse(**video_info.to_dict()),
            cached=False,
            metadata_source="api",
            fetched_at=datetime.now(timezone.utc),
        )

    except AllDownloadersFailed as e:
        # 所有下载器都失败了
        logger.error(
            f"All downloaders failed to fetch metadata for {video_id}: {e.errors}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to fetch video metadata. All downloaders unavailable: {', '.join(e.errors)}",
        )

    except DownloaderError as e:
        # 单个下载器错误
        logger.error(
            f"Failed to fetch metadata for {video_id}: {e.error_code.value} - {e.message}"
        )

        # 根据错误类型返回不同的 HTTP 状态码
        if e.error_code == ErrorCode.VIDEO_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video not found: {video_id}",
            )
        elif e.error_code == ErrorCode.VIDEO_PRIVATE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Video is private: {video_id}",
            )
        elif e.error_code == ErrorCode.VIDEO_REGION_BLOCKED:
            raise HTTPException(
                status_code=status.HTTP_451_UNAVAILABLE_FOR_LEGAL_REASONS,
                detail=f"Video is blocked in your region: {video_id}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch video metadata: {e.message}",
            )

    except Exception as e:
        # 未预期的错误
        logger.error(
            f"Unexpected error while fetching metadata for {video_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}",
        )
