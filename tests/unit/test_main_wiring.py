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
from src.core.worker import DownloadWorker


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
