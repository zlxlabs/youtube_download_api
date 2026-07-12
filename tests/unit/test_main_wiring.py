"""
main.py 服务装配（wiring）回归测试。

历史 bug：main.py 生命周期里创建了一份模块级 downloader_manager（供
video_info_routes / manual_upload_service 使用），DownloadWorker.__init__
又自建了一份独立的 DownloaderManager——两套互相独立的熔断器状态与统计
数据，导致 API/健康检查侧看到的下载器健康状态与实际执行下载的不是同一份。

本测试直接驱动 src.main.lifespan（跳过真实的 worker 任务循环与外部网络
副作用），断言 worker、通知服务、视频信息路由、人工上传服务最终引用的
是同一个 DownloaderManager 实例。
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

# 确保 src.main 可被 import：Settings.api_key 是必填项，模块级代码
# （包含 `if get_settings().manual_upload_enabled:` 路由注册判断）会在
# import 时立即调用一次 get_settings()。测试进程内若从未 import 过
# src.main，这里补一个占位 API_KEY 保证 import 不因缺配置报错；测试内
# 实际使用的 Settings 会在下面通过 monkeypatch 独立注入，互不影响。
os.environ.setdefault("API_KEY", "test-wiring-placeholder-key")

import pytest

import src.main as main_module
from src.api import video_info_routes
from src.config import Settings
from src.core.ip_ban_models import IPBanLevel
from src.core.worker import DownloadWorker
from src.db.database import Database
from src.services.notify import NotificationService


@pytest.fixture
def wiring_settings(tmp_path: Path) -> Settings:
    """构造一份最小可用的测试配置：不触网（webhook 为空、非 debug 日志落盘）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        api_key="test-wiring-key",
        wecom_webhook_url="",
        debug=True,
        data_dir=data_dir,
        file_retention_days=1,
        task_interval_min=5,
        task_interval_max=10,
        dry_run=True,
        manual_upload_enabled=True,
    )


@pytest.mark.asyncio
async def test_lifespan_wires_single_shared_downloader_manager(
    monkeypatch: pytest.MonkeyPatch, wiring_settings: Settings
) -> None:
    """
    构造 app 并驱动一次 lifespan 启动流程后，worker / 通知服务 / 视频信息
    路由 / 人工上传服务应引用同一个 DownloaderManager 实例（is 断言）。
    """
    # zlx_ops_sdk.init 在没有 SENTRY_DSN 时本就 fail-open、不触网，这里
    # 显式清空环境变量以保证行为在任意机器上都一致。
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    # lifespan() 内部通过模块全局名 get_settings 调用，patch 该名字即可
    # 让本次启动流程使用我们构造的测试配置，不依赖真实环境变量 / .env。
    monkeypatch.setattr(main_module, "get_settings", lambda: wiring_settings)

    # 跳过真实的 worker 任务循环（本测试只关心构造期的依赖注入是否正确，
    # 不需要真的处理任务），避免引入不必要的等待与副作用。
    monkeypatch.setattr(DownloadWorker, "start", AsyncMock())

    async with main_module.lifespan(main_module.app):
        assert main_module.downloader_manager is not None
        assert main_module.download_worker is not None
        assert main_module.notify_service is not None

        shared_manager = main_module.downloader_manager

        # Worker 实际执行下载所用的 manager 必须与全局共享实例是同一个对象。
        assert main_module.download_worker.downloader_manager is shared_manager

        # 通知服务展示的下载器统计/熔断器状态必须来自同一份数据。
        assert main_module.notify_service.downloader_manager is shared_manager

        # 视频信息路由查询元数据所用的 manager 也必须是同一个对象。
        assert video_info_routes.get_downloader_manager() is shared_manager

        # 人工上传服务（manual_upload_enabled=True 时）同样应共享同一实例。
        if main_module.manual_upload_service is not None:
            assert main_module.manual_upload_service.downloader_manager is shared_manager


@pytest.mark.asyncio
async def test_lifespan_restores_ip_ban_state_before_startup_notification(
    monkeypatch: pytest.MonkeyPatch, wiring_settings: Settings
) -> None:
    """
    P2 回归测试：startup 企微通知读到的必须是"恢复后"的熔断器状态。

    历史 bug：src/main.py 的 lifespan 里，
    `asyncio.create_task(download_worker.start())` 和
    `await notify_service.notify_startup(...)` 之间没有任何真正让出事件
    循环的 await 点（scheduler.start() 是同步调用，notify_startup 内部
    也全是同步操作）。IP 熔断状态的启动恢复逻辑（原来只在
    DownloadWorker.start() 内部触发）根本没机会在 notify_startup 读取
    熔断器状态之前跑完，导致通知里看到的永远是
    IPBanCircuitBreaker.__init__ 的初始值 NORMAL，而不是持久化恢复后的
    真实状态（这是确定性 bug，不是偶发竞态）。

    验证方式：预先向 lifespan 即将连接的同一个 db 文件写入 FULLY_BANNED
    持久化状态，驱动一次 lifespan 启动流程，用 monkeypatch 替换
    NotificationService.notify_startup 为一个记录用的桩函数，捕获它被
    调用时 ip_ban_breaker.get_current_level() 的返回值，断言捕获到的是
    FULLY_BANNED 而不是 NORMAL。
    """
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(main_module, "get_settings", lambda: wiring_settings)

    # 用独立的 Database 实例连到 lifespan 内部会用到的同一个 db 文件路径，
    # 提前写入 FULLY_BANNED 状态（模拟"容器重启前正处于熔断中"），
    # 再断开连接，避免和 lifespan 自己的连接冲突。
    seed_db = Database(wiring_settings.db_path)
    await seed_db.connect()
    await seed_db.save_ip_ban_state(
        current_level=IPBanLevel.FULLY_BANNED,
        banned_at=datetime.now() - timedelta(minutes=5),
        last_attempt_at=None,
        failed_attempts=1,
    )
    await seed_db.disconnect()

    # 跳过真实的 worker 任务循环（与上面的 wiring 测试一致）。恢复逻辑
    # 现在由 main.py 在 asyncio.create_task(start()) 之前显式 await
    # 调用 restore_persisted_state()，即使 start() 本身被整个 mock 掉，
    # 恢复动作依然会真实执行——这是本次重构在可测试性上的额外收益。
    monkeypatch.setattr(DownloadWorker, "start", AsyncMock())

    # 用桩函数替换 notify_startup：只记录被调用瞬间 ip_ban_breaker 的状态，
    # 不真正执行原方法体（避免真实发送企微 webhook），这样断言的是
    # "notify_startup 被调用时能读到的状态"，精确对应 bug 描述的读取时机。
    captured_levels: list[IPBanLevel] = []

    async def fake_notify_startup(self, version, ip_ban_breaker=None) -> None:
        assert ip_ban_breaker is not None, "测试期望 ip_ban_breaker 被传入"
        captured_levels.append(ip_ban_breaker.get_current_level())

    monkeypatch.setattr(NotificationService, "notify_startup", fake_notify_startup)

    async with main_module.lifespan(main_module.app):
        pass

    assert captured_levels == [IPBanLevel.FULLY_BANNED], (
        f"startup 通知读到的熔断状态应为恢复后的 FULLY_BANNED，实际捕获到: "
        f"{captured_levels}（NORMAL 说明恢复逻辑未在读取前完成，bug 复现）"
    )
