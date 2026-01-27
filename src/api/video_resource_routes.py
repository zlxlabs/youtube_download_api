"""
视频资源管理 API 路由模块。

提供视频资源的列表、详情、删除等管理功能。
"""

from pathlib import Path
from typing import Annotated, Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import ApiKeyDep
from src.api.schemas import ErrorResponse
from src.db.database import get_database, Database
from src.services.file_service import FileService
from src.utils.logger import logger


# Router for video resource management
router = APIRouter(prefix="/api/v1/video-resources", tags=["Video Resources"])


# Service dependency - will be set during app startup
_file_service: Optional[FileService] = None


def set_file_service(file_service: FileService) -> None:
    """
    设置文件服务实例用于依赖注入。

    Args:
        file_service: 文件服务实例
    """
    global _file_service
    _file_service = file_service


def get_file_service() -> FileService:
    """获取文件服务实例。"""
    if _file_service is None:
        raise RuntimeError("File service not initialized")
    return _file_service


FileServiceDep = Annotated[FileService, Depends(get_file_service)]
DatabaseDep = Annotated[Database, Depends(get_database)]


# ==================== Video Resource Endpoints ====================


@router.get(
    "",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="列出视频资源",
    description="列出所有视频资源，支持搜索和分页。",
)
async def list_video_resources(
    _: ApiKeyDep,
    db: DatabaseDep,
    search: Optional[str] = Query(None, description="搜索关键词（匹配 video_id 或标题）"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> dict[str, Any]:
    """
    列出视频资源。

    返回视频资源列表，包含文件统计信息。
    """
    try:
        resources, total = await db.list_video_resources(
            search=search,
            limit=limit,
            offset=offset,
        )

        logger.info(f"Listed {len(resources)} video resources (search={search}, total={total})")

        return {
            "resources": resources,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except aiosqlite.Error as e:
        logger.error(f"Database error during video resource listing: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during video resource listing: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.get(
    "/{video_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Video resource not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="获取视频资源详情",
    description="获取指定视频资源的完整信息，包括文件列表和任务历史。",
)
async def get_video_resource_detail(
    video_id: str,
    _: ApiKeyDep,
    db: DatabaseDep,
) -> dict[str, Any]:
    """
    获取视频资源详情。

    包含视频信息、关联文件列表、最近任务历史。
    """
    try:
        detail = await db.get_video_resource_detail(video_id)

        if not detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video resource not found: {video_id}",
            )

        logger.info(f"Fetched video resource detail: {video_id}")
        return detail

    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error(f"Database error during video resource detail fetch: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during video resource detail fetch: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.delete(
    "/{video_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Video resource not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="删除视频资源",
    description="删除指定视频资源及其关联文件。注意：任务记录会保留用于审计。",
)
async def delete_video_resource(
    video_id: str,
    _: ApiKeyDep,
    db: DatabaseDep,
    file_service: FileServiceDep,
) -> dict[str, Any]:
    """
    删除视频资源。

    级联删除关联的文件记录和物理文件。
    保留任务记录作为审计日志。
    """
    try:
        # 检查资源是否存在
        detail = await db.get_video_resource_detail(video_id)
        if not detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video resource not found: {video_id}",
            )

        # 删除数据库记录并获取文件路径列表
        file_paths = await db.delete_video_resource(video_id)

        # 物理删除文件
        deleted_count = 0
        failed_count = 0
        for file_path in file_paths:
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
                    deleted_count += 1
                    logger.debug(f"Deleted file: {file_path}")
            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to delete file {file_path}: {e}")

        logger.info(
            f"Deleted video resource: {video_id} "
            f"(deleted {deleted_count} files, {failed_count} failed)"
        )

        return {
            "video_id": video_id,
            "message": "Video resource deleted successfully",
            "files_deleted": deleted_count,
            "files_failed": failed_count,
        }

    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error(f"Database error during video resource deletion: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during video resource deletion: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
