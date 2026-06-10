"""
人工上传 API 路由模块。

提供人工上传音频文件的 REST API 接口。
"""

from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from src.api.deps import ApiKeyDep, SettingsDep
from src.api.schemas import (
    ErrorResponse,
    FileInfoResponse,
    FilesResponse,
    ManualUploadListResponse,
    ManualUploadResponse,
    VideoInfoResponse,
    VideoStatusResponse,
)
from src.services.manual_upload_service import (
    AudioAlreadyExistsError,
    FileTooLargeError,
    InvalidFileFormatError,
    ManualUploadError,
    ManualUploadService,
)
from src.utils.logger import logger

router = APIRouter(prefix="/api/v1", tags=["Manual Upload"])

_manual_upload_service: Optional[ManualUploadService] = None


def set_manual_upload_service(service: ManualUploadService) -> None:
    global _manual_upload_service
    _manual_upload_service = service


def get_manual_upload_service() -> ManualUploadService:
    if _manual_upload_service is None:
        raise RuntimeError("Manual upload service not initialized")
    return _manual_upload_service


ManualUploadServiceDep = Annotated[ManualUploadService, Depends(get_manual_upload_service)]


@router.post(
    "/manual-upload",
    response_model=ManualUploadResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request or file format"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        409: {"model": ErrorResponse, "description": "Audio file already exists"},
        413: {"model": ErrorResponse, "description": "File too large"},
        422: {"model": ErrorResponse, "description": "Transcode failed"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
    summary="Upload audio file manually",
    description=(
        "Upload an audio or video file for a YouTube video. "
        "The file will be transcoded to m4a format and stored. "
        "Metadata will be automatically fetched from YouTube if not provided."
    ),
)
async def manual_upload(
    request: Request,
    _: ApiKeyDep,
    service: ManualUploadServiceDep,
    settings: SettingsDep,
    file: UploadFile = File(..., description="Audio or video file to upload"),
    video_url: str = Form(..., description="YouTube video URL"),
    title: Optional[str] = Form(None, description="Video title (optional)"),
    author: Optional[str] = Form(None, description="Video author (optional)"),
    duration: Optional[int] = Form(None, description="Video duration in seconds (optional)"),
    channel_id: Optional[str] = Form(None, description="Channel ID (optional)"),
    description: Optional[str] = Form(None, description="Video description (optional)"),
) -> ManualUploadResponse:
    # 在解析/落盘 multipart body 之前先按 Content-Length 拒绝超大请求，
    # 防止恶意或误操作的大文件把临时盘写满（service 层的 size 校验在解析之后才生效）
    content_length = request.headers.get("content-length")
    if content_length:
        max_bytes = settings.manual_upload_max_size_mb * 1024 * 1024
        # 预留 1MB 给 multipart 边界与表单字段
        if int(content_length) > max_bytes + 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body too large (max {settings.manual_upload_max_size_mb}MB)",
            )

    try:
        manual_metadata = None
        if any([title, author, duration, channel_id, description]):
            manual_metadata = {}
            if title:
                manual_metadata["title"] = title
            if author:
                manual_metadata["author"] = author
            if duration is not None:
                manual_metadata["duration"] = duration
            if channel_id:
                manual_metadata["channel_id"] = channel_id
            if description:
                manual_metadata["description"] = description

        result = await service.handle_upload(
            video_url=video_url,
            uploaded_file=file,
            manual_metadata=manual_metadata,
        )

        video_info_response = None
        if result["video_info"]:
            video_info_response = VideoInfoResponse(
                title=result["video_info"].title,
                author=result["video_info"].author,
                channel_id=result["video_info"].channel_id,
                duration=result["video_info"].duration,
                description=result["video_info"].description,
                upload_date=result["video_info"].upload_date,
                view_count=result["video_info"].view_count,
                thumbnail=result["video_info"].thumbnail,
            )

        audio_file = result["audio_file"]
        transcript_file = result.get("transcript_file")

        files_response = FilesResponse(
            audio=FileInfoResponse(
                url=f"/api/v1/files/{audio_file.id}.m4a",
                size=audio_file.size,
                format=audio_file.format,
                bitrate=int(audio_file.quality) if audio_file.quality else None,
                language=None,
            ),
            transcript=FileInfoResponse(
                url=f"/api/v1/files/{transcript_file.id}.{transcript_file.format}",
                size=transcript_file.size,
                format=transcript_file.format,
                bitrate=None,
                language=transcript_file.language,
            )
            if transcript_file
            else None,
        )

        message = "File uploaded and processed successfully"
        if transcript_file:
            message += ". Video now has both audio and transcript."

        return ManualUploadResponse(
            task_id=None,
            status="completed",
            video_id=result["video_id"],
            video_url=video_url,
            cache_hit=False,
            upload_source="manual",
            video_info=video_info_response,
            files=files_response,
            original_format=result["original_format"],
            metadata_source=result["metadata_source"],
            message=message,
        )

    except AudioAlreadyExistsError as e:
        logger.warning(f"Upload rejected: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "AUDIO_ALREADY_EXISTS",
                "message": str(e),
                "video_id": e.video_id,
                "existing_source": e.existing_source,
            },
        )
    except InvalidFileFormatError as e:
        logger.warning(f"Invalid file format: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_FILE_FORMAT", "message": str(e)},
        )
    except FileTooLargeError as e:
        logger.warning(f"File too large: {e}")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "FILE_TOO_LARGE", "message": str(e)},
        )
    except ManualUploadError as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "UPLOAD_FAILED", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"Unexpected error during manual upload: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.get(
    "/video-status/{video_id}",
    response_model=VideoStatusResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
    summary="Check video resource status",
    description="Check whether a video already has audio and/or transcript files.",
)
async def get_video_status(
    video_id: str,
    _: ApiKeyDep,
    service: ManualUploadServiceDep,
) -> VideoStatusResponse:
    try:
        status_data = await service.get_video_status(video_id)
        return VideoStatusResponse(**status_data)
    except Exception as e:
        logger.error(f"Error getting video status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.get(
    "/manual-uploads",
    response_model=ManualUploadListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
    summary="List manual uploads",
    description="List all manually uploaded audio files with pagination.",
)
async def list_manual_uploads(
    _: ApiKeyDep,
    service: ManualUploadServiceDep,
    limit: int = Query(20, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
) -> ManualUploadListResponse:
    try:
        result = await service.list_manual_uploads(limit=limit, offset=offset)
        return ManualUploadListResponse(**result)
    except Exception as e:
        logger.error(f"Error listing manual uploads: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.delete(
    "/manual-uploads/{video_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Manual upload not found"},
    },
    summary="Delete manual upload",
    description="Delete a manually uploaded audio file.",
)
async def delete_manual_upload(
    video_id: str,
    _: ApiKeyDep,
    service: ManualUploadServiceDep,
) -> JSONResponse:
    try:
        success = await service.delete_manual_upload(video_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manual upload not found for this video",
            )

        return JSONResponse(
            content={
                "video_id": video_id,
                "message": "Manual upload deleted successfully",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting manual upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
