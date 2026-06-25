"""
FastAPI application entry point.

Initializes the application, services, and background workers.
"""

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import fastapi.responses
import httpx
import zlx_ops_sdk
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src import __version__
from src.api.routes import router as api_router
from src.api.routes import set_services
from src.api.manual_upload_routes import router as manual_upload_router
from src.api.manual_upload_routes import set_manual_upload_service
from src.api.video_resource_routes import router as video_resource_router
from src.api.video_resource_routes import set_file_service as set_vr_file_service
from src.api.settings_routes import router as settings_router
from src.api.video_info_routes import router as video_info_router
from src.api.video_info_routes import set_services as set_video_info_services
from src.api.schemas import ComponentStatus, HealthResponse, QueueStatus
from src.config import Settings, get_settings
from src.core.worker import DownloadWorker
from src.db.database import Database, set_database
from src.downloaders.manager import DownloaderManager
from src.services.callback_service import CallbackService
from src.services.file_service import FileService
from src.services.manual_upload_service import ManualUploadService
from src.services.metrics import MetricsCollector
from src.services.notify import NotificationService
from src.services.task_service import TaskService
from src.services.transcode_service import TranscodeService
from src.utils.logger import logger, setup_logger


# Global instances
db: Database | None = None
task_service: TaskService | None = None
file_service: FileService | None = None
callback_service: CallbackService | None = None
notify_service: NotificationService | None = None
transcode_service: TranscodeService | None = None
downloader_manager: DownloaderManager | None = None
manual_upload_service: ManualUploadService | None = None
download_worker: DownloadWorker | None = None
worker_task: asyncio.Task | None = None
scheduler: AsyncIOScheduler | None = None
startup_time: float = 0
metrics_collector: MetricsCollector | None = None
config_warnings: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown of services, database, and background workers.
    """
    global db, task_service, file_service, callback_service, notify_service
    global transcode_service, downloader_manager, manual_upload_service
    global download_worker, worker_task, scheduler, startup_time
    global metrics_collector, config_warnings

    settings = get_settings()

    # Setup logging
    log_dir = settings.data_dir / "logs" if not settings.debug else None
    setup_logger(log_dir=log_dir, debug=settings.debug)

    # Initialize error reporting (GlitchTip via zlx_ops_sdk).
    # DSN defaults to env SENTRY_DSN, release defaults to repo@gitSHA, fail-open.
    zlx_ops_sdk.init(
        "youtube-download-api",
        repo="zj1123581321/youtube_download_api",
        server="n305",
        environment="prod",
    )

    logger.info(f"Starting YouTube Audio API v{__version__}")

    # Validate configuration consistency
    config_warnings = settings.validate_consistency()
    if config_warnings:
        for warning in config_warnings:
            logger.warning(f"[config] {warning}")
        logger.warning(
            f"[config] {len(config_warnings)} configuration warning(s) detected"
        )

    # Initialize metrics collector
    metrics_collector = MetricsCollector()
    metrics_collector.set_config_warnings(len(config_warnings))

    # Ensure directories exist
    settings.ensure_directories()

    # Cleanup stale temporary files from previous runs
    await _cleanup_stale_temp_files(settings)

    # Initialize database with error handling
    # 数据库是关键组件，连接失败应该阻止应用启动
    db = Database(settings.db_path)
    try:
        await db.connect()
        set_database(db)  # 设置全局数据库实例供依赖注入使用
        logger.info("Database connected successfully")
    except Exception as e:
        logger.critical(f"Failed to connect to database: {e}")
        raise RuntimeError(f"Database initialization failed: {e}") from e

    # Reset any interrupted downloads with error handling
    try:
        await db.reset_downloading_tasks()
    except Exception as e:
        logger.error(f"Failed to reset downloading tasks: {e}")
        # 非关键操作，继续启动但记录错误

    # Initialize services with error handling
    try:
        file_service = FileService(db, settings)
        task_service = TaskService(db, settings, file_service)
        callback_service = CallbackService(db, file_service)
        notify_service = NotificationService(settings, db)

        # 初始化下载器管理器（用于元数据获取和下载）
        downloader_manager = DownloaderManager(settings, db)

        if settings.manual_upload_enabled:
            transcode_service = TranscodeService()
            manual_upload_service = ManualUploadService(
                db=db,
                file_service=file_service,
                transcode_service=transcode_service,
                downloader_manager=downloader_manager,
                settings=settings,
            )
    except Exception as e:
        logger.critical(f"Failed to initialize services: {e}")
        await db.disconnect()
        raise RuntimeError(f"Service initialization failed: {e}") from e

    # Set services for API routes
    set_services(task_service, file_service)
    set_vr_file_service(file_service)
    set_video_info_services(db, downloader_manager)
    if manual_upload_service:
        set_manual_upload_service(manual_upload_service)

    # Initialize download worker
    download_worker = DownloadWorker(
        db=db,
        settings=settings,
        task_service=task_service,
        file_service=file_service,
        callback_service=callback_service,
        notify_service=notify_service,
        metrics_collector=metrics_collector,
    )

    # Link downloader manager to notification service (for stats in notifications)
    notify_service.downloader_manager = download_worker.downloader_manager

    # Restore pending tasks to queue
    await task_service.restore_pending_tasks()

    # Start background worker
    # TODO: 未来支持多 Worker 并发时，根据 settings.download_concurrency 启动多个 worker
    # 示例实现：
    # worker_tasks = [
    #     asyncio.create_task(DownloadWorker(...).start())
    #     for _ in range(settings.download_concurrency)
    # ]
    # 注意：多 worker 需要共享同一个 task_queue，并考虑风控风险
    worker_task = asyncio.create_task(download_worker.start())

    # Setup scheduler for periodic tasks
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    # File cleanup: daily at 3 AM
    scheduler.add_job(
        file_service.cleanup_expired_files,
        "cron",
        hour=3,
        minute=0,
        id="cleanup_files",
    )

    # Health check: every 5 minutes
    scheduler.add_job(
        _check_health,
        "interval",
        minutes=5,
        id="health_check",
    )

    scheduler.start()

    # Record startup time
    startup_time = time.time()

    # Send startup notification with IP ban breaker status
    ip_ban_breaker = download_worker.ip_ban_breaker if download_worker else None
    await notify_service.notify_startup(__version__, ip_ban_breaker)

    logger.info(
        f"Application started successfully "
        f"(pot_provider={settings.pot_provider_type}, pot_token={'enabled' if settings.cdp_enable_pot_token else 'disabled'})"
    )

    yield

    # Shutdown
    logger.info("Shutting down...")

    # Calculate uptime
    uptime = int(time.time() - startup_time) if startup_time else 0

    # Collect shutdown statistics
    shutdown_stats = None
    if db:
        try:
            # 获取任务统计信息
            task_stats = await db.get_task_stats()
            shutdown_stats = {
                "total_tasks": task_stats["total"],
                "completed_tasks": task_stats["completed"],
                "failed_tasks": task_stats["failed"],
            }
        except Exception as e:
            logger.error(f"Failed to collect shutdown statistics: {e}")

    # Send shutdown notification
    if notify_service:
        await notify_service.notify_shutdown(uptime, shutdown_stats)

    # 关闭超时设置（秒）
    # 给下载任务足够时间响应取消信号，但不要无限等待
    SHUTDOWN_TIMEOUT = 5.0

    # Stop scheduler
    if scheduler:
        scheduler.shutdown()

    # Stop worker - 这会触发 downloader.cancel()
    if download_worker:
        await download_worker.stop()

    # 等待 worker task 结束，但设置超时
    # 注意：worker.stop() 已经设置了取消标志，worker 会自行停止
    # 这里只是等待它完成，不需要再次 cancel
    if worker_task:
        try:
            # 等待 worker 任务完成，设置超时避免无限等待
            await asyncio.wait_for(worker_task, timeout=SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                f"Worker task did not finish within {SHUTDOWN_TIMEOUT}s, "
                "forcing cancellation"
            )
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    # Close all downloaders (HTTP clients, browser instances, etc.)
    if downloader_manager:
        try:
            await downloader_manager.close()
            logger.info("DownloaderManager closed successfully")
        except Exception as e:
            logger.error(f"Error closing DownloaderManager: {e}")

    # Close database
    if db:
        await db.disconnect()

    logger.info("Application shutdown complete")


async def _check_health() -> None:
    """Periodic health check task."""
    global file_service, notify_service

    if not file_service or not notify_service:
        return

    # Check disk space
    if not file_service.check_disk_space(required_mb=500):
        usage = file_service.get_disk_usage()
        free_mb = usage["free_space"] // (1024 * 1024)
        await notify_service.notify_disk_space_warning(free_mb)


# Create FastAPI application
app = FastAPI(
    title="YouTube Audio API",
    description="API service for downloading YouTube audio and transcripts",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
# 鉴权依赖自定义 X-API-Key header（浏览器不会跨站自动携带），
# 因此 origins 可放开，但禁用 credentials 并显式限定方法与 header
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Include API routes
app.include_router(api_router)
app.include_router(video_resource_router)
app.include_router(video_info_router)
app.include_router(settings_router)

# Include manual upload routes and admin UI when enabled
if get_settings().manual_upload_enabled:
    app.include_router(manual_upload_router)
    # admin UI 为无鉴权静态页面，公网部署时可通过 ADMIN_UI_ENABLED=false 单独关闭
    if get_settings().admin_ui_enabled:
        admin_dir = Path(__file__).resolve().parent / "static" / "admin"
        if admin_dir.exists():
            app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin")


# ==================== Health Check Endpoint ====================


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Health check",
    description="Check the health status of the service and its components.",
)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service status, component health, and queue statistics.
    """
    global db, startup_time, download_worker, config_warnings

    settings = get_settings()
    components = ComponentStatus()
    queue = QueueStatus()

    # Check database
    try:
        if db:
            stats = await db.get_queue_stats()
            queue.pending = stats["pending"]
            queue.downloading = stats["downloading"]
    except Exception as e:
        components.database = f"error: {e}"
        logger.error(f"Database health check failed: {e}")

    # Check PO Token provider (仅在功能启用时检查)
    if settings.cdp_enable_pot_token:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{settings.pot_server_url}/ping")
                if response.status_code != 200:
                    components.pot_provider = f"unhealthy ({response.status_code})"
        except Exception as e:
            components.pot_provider = f"unreachable: {e}"
            logger.warning(f"PO Token provider health check failed: {e}")
    else:
        components.pot_provider = "disabled"

    # Check disk space
    if file_service:
        if not file_service.check_disk_space(required_mb=100):
            components.disk_space = "low"

    # IP ban state
    if download_worker and download_worker.ip_ban_breaker:
        ban_state = download_worker.ip_ban_breaker.get_state()
        components.ip_ban = ban_state.current_level.value

    # Config warnings (captured at startup)
    components.config_warnings = config_warnings

    # Calculate uptime
    uptime = int(time.time() - startup_time) if startup_time else 0

    # Determine overall status
    status = "healthy"
    if (
        "error" in components.database
        or "unreachable" in components.pot_provider
        or components.disk_space == "low"
    ):
        status = "degraded"

    return HealthResponse(
        status=status,
        version=__version__,
        components=components,
        queue=queue,
        uptime=uptime,
    )


# ==================== Metrics Endpoint ====================


@app.get(
    "/metrics",
    tags=["Monitoring"],
    summary="Prometheus metrics",
    description="Expose Prometheus metrics for monitoring.",
)
async def metrics_endpoint() -> fastapi.responses.Response:
    """
    Prometheus metrics endpoint.

    Syncs latest state from all components before generating output.
    """
    global metrics_collector, download_worker, db

    if not metrics_collector:
        return fastapi.responses.Response(
            content=b"# Metrics collector not initialized\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # Sync latest stats from components before scrape
    try:
        if download_worker:
            manager = download_worker.downloader_manager
            metrics_collector.sync_downloader_stats(manager.get_stats_summary())
            metrics_collector.sync_circuit_breaker_states(
                manager.get_circuit_breaker_states()
            )
            ban_state = download_worker.ip_ban_breaker.get_state()
            metrics_collector.sync_ip_ban_state(ban_state.to_dict())

        if db:
            queue_stats = await db.get_queue_stats()
            metrics_collector.sync_queue_stats(queue_stats)

        # 长期运行可观测性：RSS / 关键 dict size / 子进程数
        # 任务队列深度从 task_service 取（避免 metrics 模块反向依赖 task_service）
        queue_size: Optional[int] = None
        try:
            if task_service is not None:
                queue_size = task_service.task_queue.qsize()
        except Exception:
            queue_size = None
        metrics_collector.sync_runtime_state(task_queue_size=queue_size)
    except Exception as e:
        logger.warning(f"Error syncing metrics: {e}")

    output = metrics_collector.generate_metrics()
    return fastapi.responses.Response(
        content=output,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ==================== Root Endpoint ====================


@app.get("/", include_in_schema=False)
async def root() -> dict:
    """Redirect to docs."""
    return {
        "service": "YouTube Audio API",
        "version": __version__,
        "docs": "/docs",
    }


# ==================== CLI Entry Point ====================


def main() -> None:
    """Run the application using uvicorn."""
    import uvicorn

    settings = get_settings()

    # Windows 下 reload 模式会使用 SelectorEventLoop，不支持子进程
    # 这会导致 Playwright (CDP) 无法启动，因此在 Windows 上禁用 reload
    use_reload = settings.debug and sys.platform != "win32"

    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=use_reload,
        log_level="debug" if settings.debug else "info",
    )


async def _cleanup_stale_temp_files(settings: Settings) -> None:
    """
    清理过期的 CDP 临时文件。

    在应用启动时自动执行，清理超过 1 小时的临时 cookie 文件，
    防止文件积累占用磁盘空间。
    """
    tmp_dir = settings.data_dir / "tmp"

    if not tmp_dir.exists():
        return

    now = time.time()
    cleaned_count = 0

    # 清理超过 1 小时的 CDP cookie 文件
    for cookie_file in tmp_dir.glob("cdp_*.cookies.txt"):
        try:
            # 清理超过 1 小时的文件
            if now - cookie_file.stat().st_mtime > 3600:
                cookie_file.unlink()
                cleaned_count += 1
        except Exception as e:
            logger.warning(f"[cleanup] Failed to remove {cookie_file}: {e}")

    if cleaned_count > 0:
        logger.info(f"[cleanup] Removed {cleaned_count} stale CDP cookie files")


if __name__ == "__main__":
    main()
