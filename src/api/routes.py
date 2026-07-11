"""
API routes module.

Defines all REST API endpoints for the YouTube Audio API.
"""

import asyncio
from typing import Annotated, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from src.api.deps import ApiKeyDep
from src.api.schemas import (
    CancelTaskResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    ErrorResponse,
    TaskListResponse,
    TaskResponse,
    VideoNotDownloadableResponse,
)
from src.db.models import TaskStatus
from src.services.file_service import FileService, FileOperationError
from src.services.task_service import TaskService, VideoNotDownloadableError
from src.utils.helpers import sanitize_filename
from src.utils.logger import logger


# Router for API v1
router = APIRouter(prefix="/api/v1", tags=["Tasks"])


# Service dependencies - these will be set during app startup
_task_service: Optional[TaskService] = None
_file_service: Optional[FileService] = None


def set_services(task_service: TaskService, file_service: FileService) -> None:
    """
    Set service instances for dependency injection.

    Args:
        task_service: Task service instance.
        file_service: File service instance.
    """
    global _task_service, _file_service
    _task_service = task_service
    _file_service = file_service


def get_task_service() -> TaskService:
    """Get task service instance."""
    if _task_service is None:
        raise RuntimeError("Task service not initialized")
    return _task_service


def get_file_service() -> FileService:
    """Get file service instance."""
    if _file_service is None:
        raise RuntimeError("File service not initialized")
    return _file_service


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
FileServiceDep = Annotated[FileService, Depends(get_file_service)]


# ==================== Task Endpoints ====================


@router.post(
    "/tasks",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": TaskResponse, "description": "Task already exists"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        422: {
            "model": VideoNotDownloadableResponse,
            "description": "Video is not downloadable (live stream, upcoming premiere, "
            "unavailable, private, or region blocked)",
        },
    },
    summary="Create download task",
    description="Create a new YouTube audio download task or return existing one.",
)
async def create_task(
    request: CreateTaskRequest,
    _: ApiKeyDep,
    task_service: TaskServiceDep,
) -> TaskResponse:
    """
    Create a new download task.

    If a task for the same video already exists and is active,
    returns the existing task with a message.
    """
    try:
        response = await task_service.create_task(request)

        # If task already existed, return 200 instead of 201
        if response.message == "Task already exists":
            # Note: FastAPI doesn't easily support dynamic status codes,
            # but the response will indicate it's an existing task
            pass

        logger.info(f"Task created/found: {response.task_id}")
        return response

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except VideoNotDownloadableError as e:
        # 前置检查明确判定视频不可下载（直播/预约首播/不可用/私享/地区限制）
        logger.warning(
            f"Task creation rejected by precheck for video {e.video_id}: "
            f"{e.error_code.value} - {e.message}"
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": e.error_code.value,
                "message": e.message,
                "video_id": e.video_id,
            },
        )
    except asyncio.TimeoutError:
        logger.error("Task creation timed out")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable, please try again later",
        )
    except aiosqlite.Error as e:
        logger.error(f"Database error during task creation: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during task creation: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.get(
    "/tasks",
    response_model=TaskListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
    },
    summary="List tasks",
    description="List download tasks with optional filtering and pagination.",
)
async def list_tasks(
    _: ApiKeyDep,
    task_service: TaskServiceDep,
    status_filter: Optional[TaskStatus] = Query(
        None, alias="status", description="Filter by task status"
    ),
    search: Optional[str] = Query(
        None, description="Search keyword (matches video_id or video title)"
    ),
    created_after: Optional[str] = Query(
        None, description="Filter tasks created after this datetime (ISO 8601 format)"
    ),
    created_before: Optional[str] = Query(
        None, description="Filter tasks created before this datetime (ISO 8601 format)"
    ),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
) -> TaskListResponse:
    """List tasks with pagination and filters."""
    try:
        # Parse datetime strings
        created_after_dt = None
        created_before_dt = None

        if created_after:
            try:
                from datetime import datetime
                created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid created_after format: {e}",
                )

        if created_before:
            try:
                from datetime import datetime
                created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid created_before format: {e}",
                )

        return await task_service.list_tasks(
            status=status_filter,
            search=search,
            created_after=created_after_dt,
            created_before=created_before_dt,
            limit=limit,
            offset=offset,
        )
    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error(f"Database error during task listing: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during task listing: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Task not found"},
    },
    summary="Get task details",
    description="Get detailed information about a specific task.",
)
async def get_task(
    task_id: str,
    _: ApiKeyDep,
    task_service: TaskServiceDep,
) -> TaskResponse:
    """Get task by ID."""
    try:
        response = await task_service.get_task(task_id)

        if not response:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        return response
    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error(f"Database error during task retrieval: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during task retrieval: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.delete(
    "/tasks/{task_id}",
    response_model=CancelTaskResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Cannot cancel task"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Task not found"},
    },
    summary="Cancel task",
    description="Cancel a pending download task.",
)
async def cancel_task(
    task_id: str,
    _: ApiKeyDep,
    task_service: TaskServiceDep,
) -> CancelTaskResponse:
    """Cancel a pending task."""
    try:
        response = await task_service.cancel_task(task_id)

        if not response:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        # task_id 在取消操作中不应为 None
        if response.task_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid response: task_id is None",
            )

        return CancelTaskResponse(
            task_id=response.task_id,
            status=response.status,
            message=response.message or "Task cancelled successfully",
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error(f"Database error during task cancellation: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during task cancellation: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


# ==================== File Endpoints ====================


@router.get(
    "/files/{file_id_with_ext}",
    responses={
        404: {"model": ErrorResponse, "description": "File not found"},
    },
    summary="Download file",
    description="Download an audio or transcript file by ID. Supports URL with extension (e.g., /files/uuid.m4a). No authentication required. Use ?filename=custom_name.ext to specify custom download filename.",
    tags=["Files"],
)
async def download_file(
    file_id_with_ext: str,
    file_service: FileServiceDep,
    filename: Optional[str] = Query(None, description="Custom download filename"),
) -> FileResponse:
    """
    Download a file by ID.

    Supports URLs with or without extension:
    - /files/{uuid}
    - /files/{uuid}.m4a
    - /files/{uuid}.srt

    Optional query parameter:
    - filename: Custom download filename (e.g., ?filename=video_title.srt)

    This endpoint is public (no API key required) but uses UUID file IDs
    to prevent enumeration attacks.
    """
    try:
        # Extract file_id from path (strip extension if present)
        # UUID format: 8-4-4-4-12 = 36 characters
        if len(file_id_with_ext) > 36 and "." in file_id_with_ext[36:]:
            file_id = file_id_with_ext[:36]
        else:
            file_id = file_id_with_ext.split(".")[0] if "." in file_id_with_ext else file_id_with_ext

        result = await file_service.get_file(file_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )

        file_record, file_path = result

        # Determine media type
        media_type = "application/octet-stream"
        if file_record.format == "m4a":
            media_type = "audio/mp4"
        elif file_record.format == "json3" or file_record.format == "json":
            media_type = "application/json"
        elif file_record.format == "webm":
            media_type = "audio/webm"

        # Use custom filename if provided, otherwise use original filename
        # 公开端点：过滤路径分隔符与控制字符，防止 Content-Disposition 注入
        download_filename = sanitize_filename(filename) if filename else file_record.filename

        return FileResponse(
            path=file_path,
            filename=download_filename,
            media_type=media_type,
        )
    except HTTPException:
        raise
    except FileOperationError as e:
        logger.error(f"File operation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File operation failed",
        )
    except aiosqlite.Error as e:
        logger.error(f"Database error during file retrieval: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during file download: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
