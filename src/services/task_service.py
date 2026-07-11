"""
Task service module.

Handles task creation, querying, and resource-based deduplication.
Implements file-level caching: if requested resources already exist,
returns them without creating a new download task.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from src.api.schemas import (
    CreateTaskRequest,
    ErrorInfoResponse,
    FileInfoResponse,
    FilesResponse,
    RequestModeResponse,
    ResultInfoResponse,
    TaskListResponse,
    TaskResponse,
    VideoInfoResponse,
)
from src.config import Settings
from src.db.database import Database
from src.db.models import (
    CallbackStatus,
    ErrorCode,
    FileType,
    Task,
    TaskStatus,
    TaskPriority,
    calculate_queue_priority,
)
from src.downloaders.exceptions import DownloaderError
from src.downloaders.manager import DownloaderManager
from src.services.file_service import FileService
from src.utils.helpers import extract_video_id
from src.utils.logger import logger


# 前置检查中，元数据获取抛出 DownloaderError 时，哪些错误码代表视频已明确不可下载，
# 应该拒绝创建任务。不在此集合内的错误码（如网络错误、限流等）一律 fail-open。
_PRECHECK_REJECT_ERROR_CODES = frozenset(
    {
        ErrorCode.VIDEO_UNAVAILABLE,
        ErrorCode.VIDEO_PRIVATE,
        ErrorCode.VIDEO_REGION_BLOCKED,
        ErrorCode.VIDEO_LIVE_STREAM,
    }
)


class VideoNotDownloadableError(Exception):
    """
    视频不可下载错误。

    在 create_task 的前置检查阶段，一旦通过元数据探测明确判定视频不可下载
    （直播中/预约首播/已被删除/私享/地区限制等）时抛出。
    API 层捕获后返回 HTTP 422，避免下游客户端异步等待到下载阶段才收到失败反馈。
    """

    def __init__(self, video_id: str, error_code: ErrorCode, message: str):
        self.video_id = video_id
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class TaskService:
    """
    Service for managing download tasks.

    Implements resource-based caching: checks if requested files already exist
    before creating download tasks. Files are shared across tasks for the same video.
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        file_service: FileService,
        downloader_manager: Optional[DownloaderManager] = None,
    ):
        """
        Initialize task service.

        Args:
            db: Database instance.
            settings: Application settings.
            file_service: File service for resource management.
            downloader_manager: Optional downloader manager, used for the
                pre-creation availability check (precheck). When None (default)
                or when ``settings.precheck_enabled`` is False, precheck is
                fully skipped so existing callers/tests are unaffected.
        """
        self.db = db
        self.settings = settings
        self.file_service = file_service
        self.downloader_manager = downloader_manager
        # Task queue for worker to consume
        # 使用优先级队列：(priority, task_id)
        # priority=0: 新任务（高优先级）
        # priority=1: 重试任务（低优先级）
        self._task_queue: asyncio.PriorityQueue[tuple[int, str]] = asyncio.PriorityQueue()

    @property
    def task_queue(self) -> asyncio.PriorityQueue[tuple[int, str]]:
        """Get the task queue."""
        return self._task_queue

    async def create_task(self, request: CreateTaskRequest) -> TaskResponse:
        """
        Create a new download task or return cached resources.

        Resource-based caching logic:
        1. Check if requested files already exist for this video
        2. If all needed files exist -> return immediately with cached resources
        3. If some files missing -> create task to download missing files
        4. If active task exists for same video -> return existing task

        Args:
            request: Task creation request.

        Returns:
            TaskResponse with task details or cached resources.
        """
        video_id = extract_video_id(request.video_url)
        if not video_id:
            raise ValueError("Invalid YouTube URL")

        # Check existing resources for this video
        existing_files = await self.file_service.get_all_files_for_video(video_id)
        existing_audio = existing_files.get("audio")
        existing_transcript = existing_files.get("transcript")

        # Check if video is known to have no native transcript
        video_resource = await self.db.get_video_resource(video_id)
        video_has_no_transcript = (
            video_resource is not None
            and video_resource.has_native_transcript is False
        )

        # Determine what we need
        need_audio = request.include_audio and existing_audio is None
        need_transcript = request.include_transcript and existing_transcript is None

        # Special case: user requests transcript, but video is known to have none
        # If audio exists, return it as fallback (no need to create new task)
        if (
            need_transcript
            and video_has_no_transcript
            and existing_audio is not None
        ):
            logger.info(
                f"Cache hit for video {video_id}: no native transcript, "
                f"returning existing audio as fallback"
            )
            return await self._build_cached_response(
                video_id=video_id,
                video_url=request.video_url,
                request=request,
                audio_file=existing_audio,
                transcript_file=None,
                audio_fallback=True,
            )

        # If all requested resources exist, return immediately (cache hit)
        if not need_audio and not need_transcript:
            logger.info(f"Cache hit for video {video_id}: all requested resources exist")
            return await self._build_cached_response(
                video_id=video_id,
                video_url=request.video_url,
                request=request,
                audio_file=existing_audio,
                transcript_file=existing_transcript,
            )

        # Check for active (pending/downloading) task for same video
        active_task = await self.db.get_active_task_by_video(video_id)
        if active_task:
            # 需求覆盖校验：只有当活跃任务已经涵盖本次请求需要的全部资源时才复用。
            # 例：活跃任务只请求了音频（include_transcript=False），
            # 而新请求需要字幕，直接复用会返回一个永远不会产出字幕的任务 ID。
            # 覆盖不足时不复用，照常走后续流程创建新任务（文件级缓存会让重复部分开销很小）。
            covers_audio = (not request.include_audio) or active_task.include_audio
            covers_transcript = (not request.include_transcript) or active_task.include_transcript
            if covers_audio and covers_transcript:
                logger.info(f"Found active task for video {video_id}: {active_task.id}")
                response = await self._build_task_response(active_task)
                response.message = "Task already in progress"
                return response

            logger.info(
                f"Active task {active_task.id} for video {video_id} does not cover requested "
                f"resources (requested audio={request.include_audio}/transcript={request.include_transcript}, "
                f"active task audio={active_task.include_audio}/transcript={active_task.include_transcript}); "
                f"creating a new task instead of reusing it"
            )

        # 前置检查：在真正创建任务前，拦截已知不可下载的视频（直播/预约首播/不可用等）。
        # 位置刻意放在文件缓存命中检查、活跃任务去重之后——缓存命中和复用路径不应因此增加延迟。
        # 检查本身严格 fail-open：只有明确判定不可下载时才拒绝，其余一律放行。
        await self._precheck_video_downloadable(video_id, request)

        # Ensure video resource exists
        await self.db.get_or_create_video_resource(video_id)

        # Create new task with pre-filled existing resources
        task = Task(
            id=str(uuid4()),
            video_id=video_id,
            video_url=request.video_url,
            status=TaskStatus.PENDING,
            priority=request.priority,  # 使用用户指定的优先级
            include_audio=request.include_audio,
            include_transcript=request.include_transcript,
            # Pre-fill existing resources
            audio_file_id=existing_audio.id if existing_audio else None,
            transcript_file_id=existing_transcript.id if existing_transcript else None,
            reused_audio=existing_audio is not None,
            reused_transcript=existing_transcript is not None,
            callback_url=str(request.callback_url) if request.callback_url else None,
            callback_secret=request.callback_secret,
            callback_status=CallbackStatus.PENDING if request.callback_url else None,
            created_at=datetime.now(timezone.utc),
        )

        await self.db.create_task(task)

        # 计算队列优先级（基于用户优先级和任务类型）
        queue_priority = calculate_queue_priority(
            user_priority=task.priority,
            include_audio=task.include_audio,
            include_transcript=task.include_transcript,
        )

        logger.info(
            f"Created task {task.id} for video {video_id} "
            f"(user_priority={task.priority.value}, queue_priority={queue_priority}, "
            f"need_audio={need_audio}, need_transcript={need_transcript}, "
            f"reused_audio={task.reused_audio}, reused_transcript={task.reused_transcript})"
        )

        # Add to queue for worker
        await self._task_queue.put((queue_priority, task.id))

        # Build response
        response = await self._build_task_response(task)
        return response

    async def _precheck_video_downloadable(
        self, video_id: str, request: CreateTaskRequest
    ) -> None:
        """
        创建任务前的前置校验：探测视频是否已知不可下载。

        复用 DownloaderManager.get_metadata()（先查 DB 永久缓存，未命中再按
        METADATA_PRIORITY 降级调用下载器）快速判断视频是否为直播/预约首播/
        已被删除/私享/地区限制，从而在任务创建前就能给下游明确的失败反馈，
        而不必等到异步下载流程跑到元数据阶段才暴露。

        Fail-open 设计：只有明确判定为不可下载时才拒绝；探测超时、下载器全部
        失败、或任何意外异常都视为“探测本身不可用”，直接放行，绝不能因为前置
        检查故障而降低服务可用性。

        Args:
            video_id: YouTube 视频 ID。
            request: 原始建任务请求（用于取 video_url）。

        Raises:
            VideoNotDownloadableError: 视频被明确判定为不可下载。
        """
        if self.downloader_manager is None or not self.settings.precheck_enabled:
            return

        try:
            metadata = await asyncio.wait_for(
                self.downloader_manager.get_metadata(
                    video_url=request.video_url,
                    video_id=video_id,
                    # 内容级终态错误（VIDEO_UNAVAILABLE/VIDEO_PRIVATE/...）必须让
                    # get_metadata 向上抛出，否则默认行为会把异常吞掉、返回 None，
                    # 下面的 except DownloaderError 分支永远走不到，422 拦截失效。
                    raise_content_errors=True,
                ),
                timeout=self.settings.precheck_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Precheck timed out for video {video_id} after "
                f"{self.settings.precheck_timeout}s, proceeding to create task (fail-open)"
            )
            return
        except DownloaderError as e:
            if e.error_code in _PRECHECK_REJECT_ERROR_CODES:
                logger.warning(
                    f"Precheck rejected video {video_id}: {e.error_code.value} - {e.message}"
                )
                raise VideoNotDownloadableError(
                    video_id=video_id, error_code=e.error_code, message=e.message
                ) from e
            logger.warning(
                f"Precheck metadata fetch raised non-blocking DownloaderError for video "
                f"{video_id}: {e.error_code.value} - {e.message}, proceeding to create task "
                f"(fail-open)"
            )
            return
        except Exception as e:
            # 包含 AllDownloadersFailed 之外的一切意外异常：网络错误、下载器内部 bug 等。
            # 前置检查的可用性绝不能拖累任务创建，一律 fail-open。
            logger.warning(
                f"Precheck metadata fetch failed unexpectedly for video {video_id}: "
                f"{type(e).__name__}: {e}, proceeding to create task (fail-open)"
            )
            return

        if not metadata:
            # 所有下载器都获取失败（get_metadata 内部已吞掉各下载器异常，返回 None）
            logger.warning(
                f"Precheck could not fetch metadata for video {video_id} "
                f"(all downloaders failed), proceeding to create task (fail-open)"
            )
            return

        live_status = metadata.get("live_broadcast_content")
        if live_status in ("upcoming", "live"):
            logger.warning(
                f"Precheck rejected video {video_id}: live broadcast (status={live_status})"
            )
            raise VideoNotDownloadableError(
                video_id=video_id,
                error_code=ErrorCode.VIDEO_LIVE_STREAM,
                message=(
                    f"Video is a live broadcast (status: {live_status}), "
                    "not available for download"
                ),
            )

    async def _build_cached_response(
        self,
        video_id: str,
        video_url: str,
        request: CreateTaskRequest,
        audio_file,
        transcript_file,
        audio_fallback: bool = False,
    ) -> TaskResponse:
        """
        Build response for cache hit (all resources exist).

        Args:
            video_id: YouTube video ID.
            video_url: Original video URL.
            request: Original request.
            audio_file: Existing audio FileRecord (or None).
            transcript_file: Existing transcript FileRecord (or None).
            audio_fallback: Whether audio is returned as fallback for missing transcript.

        Returns:
            TaskResponse with cached resources.
        """
        # Get video resource for metadata
        video_resource = await self.db.get_video_resource(video_id)

        # Build response
        now = datetime.now(timezone.utc)
        response = TaskResponse(
            task_id=None,  # No task created for cache hits
            cache_hit=True,  # Indicate this is a cache hit
            status=TaskStatus.COMPLETED,
            video_id=video_id,
            video_url=video_url,
            created_at=now,
            completed_at=now,
            message="Resources retrieved from cache",
            request=RequestModeResponse(
                include_audio=request.include_audio,
                include_transcript=request.include_transcript,
            ),
            result=ResultInfoResponse(
                has_transcript=transcript_file is not None,
                audio_fallback=audio_fallback,
                reused_audio=audio_file is not None if request.include_audio or audio_fallback else False,
                reused_transcript=transcript_file is not None if request.include_transcript else False,
            ),
        )

        # Add video info if available
        if video_resource and video_resource.video_info:
            info = video_resource.video_info
            response.video_info = VideoInfoResponse(
                title=info.title,
                author=info.author,
                channel_id=info.channel_id,
                duration=info.duration,
                description=info.description,
                upload_date=info.upload_date,
                view_count=info.view_count,
                thumbnail=info.thumbnail,
            )

        # Build files response
        audio_info = None
        # Include audio if requested OR if it's a fallback for missing transcript
        if audio_file and (request.include_audio or audio_fallback):
            # Generate URL with extension for better compatibility
            # Fallback to extension from filename if format field is empty
            audio_ext = audio_file.format or Path(audio_file.filename).suffix.lstrip(".") or "m4a"
            audio_info = FileInfoResponse(
                url=f"/api/v1/files/{audio_file.id}.{audio_ext}",
                size=audio_file.size,
                format=audio_file.format,
                bitrate=audio_file.quality,
            )

        transcript_info = None
        if transcript_file and request.include_transcript:
            # Generate URL with extension for better compatibility
            transcript_ext = transcript_file.format or "srt"
            transcript_info = FileInfoResponse(
                url=f"/api/v1/files/{transcript_file.id}.{transcript_ext}",
                size=transcript_file.size,
                format=transcript_file.format,
                language=transcript_file.language,
            )

        if audio_info or transcript_info:
            response.files = FilesResponse(
                audio=audio_info,
                transcript=transcript_info,
            )

        # Set expiry based on file expiry
        if audio_file:
            response.expires_at = audio_file.expires_at
        elif transcript_file:
            response.expires_at = transcript_file.expires_at

        return response

    async def get_task(self, task_id: str) -> Optional[TaskResponse]:
        """
        Get task by ID.

        Args:
            task_id: Task UUID.

        Returns:
            TaskResponse or None if not found.
        """
        task = await self.db.get_task(task_id)
        if not task:
            return None

        return await self._build_task_response(task)

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        search: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskListResponse:
        """
        List tasks with pagination and optional filtering.

        Args:
            status: Filter by task status.
            search: Search keyword (matches video_id or video title).
            created_after: Filter tasks created after this datetime.
            created_before: Filter tasks created before this datetime.
            limit: Maximum number of results (max 100).
            offset: Number of results to skip.

        Returns:
            TaskListResponse with paginated results.
        """
        # Enforce limit
        limit = min(limit, 100)

        tasks, total = await self.db.list_tasks(
            status=status,
            search=search,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )

        task_responses = []
        for task in tasks:
            response = await self._build_task_response(task)
            task_responses.append(response)

        return TaskListResponse(
            tasks=task_responses,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def cancel_task(self, task_id: str) -> Optional[TaskResponse]:
        """
        Cancel a pending task.

        Only pending tasks can be cancelled. Tasks that are already
        downloading or completed cannot be cancelled.

        Args:
            task_id: Task UUID.

        Returns:
            TaskResponse or None if task not found.

        Raises:
            ValueError: If task cannot be cancelled.
        """
        task = await self.db.get_task(task_id)
        if not task:
            return None

        if task.status != TaskStatus.PENDING:
            raise ValueError(f"Cannot cancel task with status: {task.status.value}")

        await self.db.update_task_status(task_id, TaskStatus.CANCELLED)
        logger.info(f"Task cancelled: {task_id}")

        task.status = TaskStatus.CANCELLED
        response = await self._build_task_response(task)
        response.message = "Task cancelled successfully"
        return response

    async def get_next_task(self) -> Optional[Task]:
        """
        Get next task from queue.

        优先级队列会自动按优先级排序：
        - priority=0: 新任务（高优先级，先处理）
        - priority=1: 重试任务（低优先级，后处理）

        Returns:
            Task object or None if queue is empty.
        """
        try:
            # 从优先级队列获取任务，解包 (priority, task_id)
            priority, task_id = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)
            task = await self.db.get_task(task_id)

            # Skip if task was cancelled while waiting
            if task and task.status != TaskStatus.PENDING:
                logger.debug(f"Skipping task {task_id} with status {task.status}")
                return None

            return task
        except asyncio.TimeoutError:
            return None

    # 恢复 pending 任务时每批拉取的数量。
    # 历史 bug：曾经硬编码 get_pending_tasks(limit=100) 单次拉取，堆积超过
    # 100 条时超出部分永远恢复不到队列里，导致任务静默卡死。现在改为以此
    # 批大小循环分页拉取，直到取完全部 pending 任务为止，不再有隐藏上限。
    _RESTORE_BATCH_SIZE = 100

    async def restore_pending_tasks(self) -> int:
        """
        Restore pending tasks to queue after restart.

        分批循环拉取全部 pending 任务（而非一次性限量拉取），保持
        get_pending_tasks 现有的排序语义（created_at ASC），确保堆积
        再多任务也能被完整恢复入队，不会有任务被静默遗漏。

        Returns:
            Number of tasks restored.
        """
        count = 0
        offset = 0

        while True:
            tasks = await self.db.get_pending_tasks(
                limit=self._RESTORE_BATCH_SIZE, offset=offset
            )
            if not tasks:
                break

            for task in tasks:
                # 启动时恢复的任务，重新计算队列优先级
                queue_priority = calculate_queue_priority(
                    user_priority=task.priority,
                    include_audio=task.include_audio,
                    include_transcript=task.include_transcript,
                )
                await self._task_queue.put((queue_priority, task.id))
                count += 1

            offset += len(tasks)

            # 本批数量小于批大小，说明已经是最后一批，无需再查询
            if len(tasks) < self._RESTORE_BATCH_SIZE:
                break

        if count > 0:
            logger.info(f"Restored {count} pending tasks to queue")

        return count

    async def _build_task_response(self, task: Task) -> TaskResponse:
        """
        Build TaskResponse from Task model.

        Args:
            task: Task database model.

        Returns:
            TaskResponse API model.
        """
        # Base response
        response = TaskResponse(
            task_id=task.id,
            status=task.status,
            video_id=task.video_id,
            video_url=task.video_url,
            priority=task.priority,  # 包含任务优先级
            created_at=task.created_at or datetime.now(timezone.utc),
            started_at=task.started_at,
            completed_at=task.completed_at,
            request=RequestModeResponse(
                include_audio=task.include_audio,
                include_transcript=task.include_transcript,
            ),
        )

        # Add queue position for pending tasks
        if task.status == TaskStatus.PENDING:
            position = await self.db.get_queue_position(task.id)
            response.position = position
            # Estimate wait time based on task type
            # 字幕任务使用短间隔估算，音频/混合任务使用长间隔估算
            if not task.include_audio and task.include_transcript:
                avg_interval = (
                    self.settings.transcript_interval_min + self.settings.transcript_interval_max
                ) / 2
            else:
                avg_interval = (
                    self.settings.audio_interval_min + self.settings.audio_interval_max
                ) / 2
            response.estimated_wait = int(position * avg_interval)

        # Add progress for downloading tasks
        elif task.status == TaskStatus.DOWNLOADING:
            response.progress = task.progress

        # Add video info and files for completed tasks
        elif task.status == TaskStatus.COMPLETED:
            # Get video resource for metadata
            video_resource = await self.db.get_video_resource(task.video_id)
            if video_resource and video_resource.video_info:
                info = video_resource.video_info
                response.video_info = VideoInfoResponse(
                    title=info.title,
                    author=info.author,
                    channel_id=info.channel_id,
                    duration=info.duration,
                    description=info.description,
                    upload_date=info.upload_date,
                    view_count=info.view_count,
                    thumbnail=info.thumbnail,
                )

            # Build result info
            # Determine has_transcript based on whether transcript file exists
            has_transcript = task.transcript_file_id is not None
            # audio_fallback: requested transcript only but got audio instead
            audio_fallback = (
                not task.include_audio
                and task.include_transcript
                and task.audio_file_id is not None
                and task.transcript_file_id is None
            )

            response.result = ResultInfoResponse(
                has_transcript=has_transcript,
                audio_fallback=audio_fallback,
                reused_audio=task.reused_audio,
                reused_transcript=task.reused_transcript,
            )

            # Get file info
            audio_file = None
            transcript_file = None

            if task.audio_file_id:
                audio_file = await self.db.get_file(task.audio_file_id)
            if task.transcript_file_id:
                transcript_file = await self.db.get_file(task.transcript_file_id)

            # Build files response
            if audio_file or transcript_file:
                audio_info = None
                if audio_file:
                    # Generate URL with extension for better compatibility
                    # Fallback to extension from filename if format field is empty
                    audio_ext = audio_file.format or Path(audio_file.filename).suffix.lstrip(".") or "m4a"
                    audio_info = FileInfoResponse(
                        url=f"/api/v1/files/{audio_file.id}.{audio_ext}",
                        size=audio_file.size,
                        format=audio_file.format,
                        bitrate=audio_file.quality,
                    )

                transcript_info = None
                if transcript_file:
                    # Generate URL with extension for better compatibility
                    transcript_ext = transcript_file.format or "srt"
                    transcript_info = FileInfoResponse(
                        url=f"/api/v1/files/{transcript_file.id}.{transcript_ext}",
                        size=transcript_file.size,
                        format=transcript_file.format,
                        language=transcript_file.language,
                    )

                response.files = FilesResponse(
                    audio=audio_info,
                    transcript=transcript_info,
                )

            # Set expiry
            if audio_file:
                response.expires_at = audio_file.expires_at
            elif transcript_file:
                response.expires_at = transcript_file.expires_at

        # Add error info for failed tasks
        elif task.status == TaskStatus.FAILED:
            if task.error_code:
                response.error = ErrorInfoResponse(
                    code=task.error_code,
                    message=task.error_message or "Unknown error",
                    retry_count=task.retry_count,
                )

        return response
