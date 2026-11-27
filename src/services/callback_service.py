"""
Callback service module.

Handles webhook callbacks to notify external services of task completion.
"""

import asyncio
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any, Optional

import httpx

from src.api.schemas import (
    CallbackPayload,
    ErrorInfoResponse,
    VideoInfoResponse,
)
from src.db.database import Database
from src.db.models import CallbackStatus, Task, TaskStatus
from src.services.files_view import build_files_response
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.services.file_service import FileService


class CallbackService:
    """
    Service for sending webhook callbacks.

    Implements retry logic and HMAC signature verification.
    """

    # Callback configuration
    TIMEOUT_SECONDS = 10
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 10, 20]  # seconds

    def __init__(
        self,
        db: Database,
        file_service: Optional["FileService"] = None,
        base_url: str = "",
    ):
        """
        Initialize callback service.

        Args:
            db: Database instance.
            file_service: File service for getting file info.
            base_url: Base URL for file downloads.
        """
        self.db = db
        self.file_service = file_service
        self.base_url = base_url.rstrip("/")
        self._in_flight_task_ids: set[str] = set()

    async def send_callback(self, task: Task) -> bool:
        if task.id in self._in_flight_task_ids:
            return False
        self._in_flight_task_ids.add(task.id)
        try:
            return await self._send_callback(task)
        finally:
            self._in_flight_task_ids.discard(task.id)

    async def recover_pending_callbacks(
        self, max_age_days: int = 7, limit: int = 100
    ) -> int:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - max_age_days * 86400)
        )
        cursor = await self.db.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('completed', 'failed')
              AND callback_url IS NOT NULL AND callback_url != ''
              AND callback_status = 'pending'
              AND datetime(COALESCE(completed_at, created_at)) >= datetime(?)
            ORDER BY datetime(COALESCE(completed_at, created_at)) ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        rows = await cursor.fetchall()
        processed = 0
        for row in rows:
            task = self.db._row_to_task(row)
            if task.id in self._in_flight_task_ids:
                continue
            processed += 1
            try:
                await self.send_callback(task)
            except Exception as exc:
                logger.error(
                    f"Callback recovery failed for task {task.id}: "
                    f"{type(exc).__name__}: {exc}"
                )
        return processed

    async def _send_callback(self, task: Task) -> bool:
        """
        Send webhook callback for a completed/failed task.

        区分错误类型进行智能重试：
        - 网络错误（超时、连接失败）：可重试
        - 5xx 服务端错误：可重试
        - 4xx 客户端错误：不重试（配置问题）

        Args:
            task: Task with callback_url configured.

        Returns:
            True if callback was successful.
        """
        if not task.callback_url:
            return True

        payload = await self._build_payload(task)
        success = False
        attempts = 0
        should_retry = True

        for attempt in range(self.MAX_RETRIES):
            if not should_retry:
                break

            attempts = attempt + 1
            try:
                await self._send_request(
                    url=task.callback_url,
                    payload=payload,
                    secret=task.callback_secret,
                    task_id=task.id,
                )
                success = True
                logger.info(f"Callback sent successfully for task {task.id}")
                break

            except httpx.TimeoutException as e:
                # 超时错误：可重试
                logger.warning(
                    f"Callback timeout (attempt {attempts}/{self.MAX_RETRIES}) "
                    f"for task {task.id}: {e}"
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAYS[attempt])

            except httpx.ConnectError as e:
                # 连接错误：可重试
                logger.warning(
                    f"Callback connection error (attempt {attempts}/{self.MAX_RETRIES}) "
                    f"for task {task.id}: {e}"
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAYS[attempt])

            except httpx.HTTPStatusError as e:
                # HTTP 状态错误：根据状态码决定是否重试
                status_code = e.response.status_code
                if 400 <= status_code < 500:
                    # 4xx 客户端错误：不重试（配置问题）
                    logger.error(
                        f"Callback failed with client error {status_code} "
                        f"for task {task.id}: {e}. Not retrying."
                    )
                    should_retry = False
                else:
                    # 5xx 服务端错误：可重试
                    logger.warning(
                        f"Callback server error {status_code} "
                        f"(attempt {attempts}/{self.MAX_RETRIES}) "
                        f"for task {task.id}: {e}"
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAYS[attempt])

            except Exception as e:
                # 其他未知错误：记录并重试
                logger.warning(
                    f"Callback unexpected error (attempt {attempts}/{self.MAX_RETRIES}) "
                    f"for task {task.id}: {type(e).__name__}: {e}"
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAYS[attempt])

        # Update callback status
        status = CallbackStatus.SUCCESS if success else CallbackStatus.FAILED
        await self.db.update_callback_status(task.id, status, attempts)

        if not success:
            logger.error(
                f"All callback attempts failed for task {task.id} "
                f"after {attempts} attempts"
            )

        return success

    async def _send_request(
        self,
        url: str,
        payload: CallbackPayload,
        secret: Optional[str],
        task_id: str,
    ) -> None:
        """
        Send HTTP POST request to callback URL.

        Args:
            url: Callback URL.
            payload: Callback payload.
            secret: HMAC secret for signature.
            task_id: Task ID for headers.

        Raises:
            httpx.HTTPError: If request fails.
        """
        body = payload.model_dump_json()
        timestamp = str(int(time.time()))

        headers = {
            "Content-Type": "application/json",
            "X-Task-Id": task_id,
            "X-Timestamp": timestamp,
        }

        # Add signature if secret provided
        if secret:
            signature = self._generate_signature(body.encode(), secret)
            headers["X-Signature"] = signature

        async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
            response = await client.post(url, content=body.encode(), headers=headers)
            response.raise_for_status()

    def _generate_signature(self, body: bytes, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature.

        Args:
            body: Request body bytes.
            secret: HMAC secret.

        Returns:
            Signature string prefixed with "sha256=".
        """
        signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    async def _build_payload(self, task: Task) -> CallbackPayload:
        """
        Build callback payload from task.

        Args:
            task: Task to build payload for.

        Returns:
            CallbackPayload object.
        """
        # Get file info for expires_at
        expires_at = None
        audio_file = None
        transcript_file = None

        if task.audio_file_id:
            audio_file = await self.db.get_file(task.audio_file_id)
            if audio_file:
                expires_at = audio_file.expires_at

        if task.transcript_file_id:
            transcript_file = await self.db.get_file(task.transcript_file_id)
            if transcript_file and not expires_at:
                expires_at = transcript_file.expires_at

        payload = CallbackPayload(
            task_id=task.id,
            status=task.status,
            video_id=task.video_id,
            expires_at=expires_at,
        )

        # Add video info for completed tasks
        if task.status == TaskStatus.COMPLETED:
            video_resource = await self.db.get_video_resource(task.video_id)
            if video_resource and video_resource.video_info:
                video_info = video_resource.video_info
                payload.video_info = VideoInfoResponse(
                    title=video_info.title,
                    author=video_info.author,
                    channel_id=video_info.channel_id,
                    duration=video_info.duration,
                    description=video_info.description,
                    upload_date=video_info.upload_date,
                    view_count=video_info.view_count,
                    thumbnail=video_info.thumbnail,
                )

            payload.files = build_files_response(
                audio_file,
                transcript_file,
                audio_url=lambda file: (
                    f"{self.base_url}/api/v1/files/{file.id}"
                ),
                transcript_url=lambda file: (
                    f"{self.base_url}/api/v1/files/{file.id}"
                ),
            )

        # Add error info for failed tasks
        elif task.status == TaskStatus.FAILED and task.error_code:
            payload.error = ErrorInfoResponse(
                code=task.error_code,
                message=task.error_message or "Unknown error",
                retry_count=task.retry_count,
            )

        return payload


def verify_callback_signature(body: bytes, signature: str, secret: str) -> bool:
    """
    Verify HMAC-SHA256 signature from callback.

    This function is provided for clients to verify incoming callbacks.

    Args:
        body: Request body bytes.
        signature: Signature from X-Signature header.
        secret: Shared HMAC secret.

    Returns:
        True if signature is valid.
    """
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
