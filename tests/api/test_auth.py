"""
API 鉴权强制性测试。

全量扫描所有已注册路由，确保除显式声明的公开端点外，
每个端点都强制要求 API key：

- 缺少 key -> 401
- 错误 key -> 403

新增路由若忘记声明 ApiKeyDep，本测试会立即失败。
"""

import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.config import Settings, get_settings

TEST_API_KEY = "test-api-key-12345"

# 设计上无需鉴权的公开端点（新增公开端点必须在此显式登记）
PUBLIC_PATHS = {
    "/api/v1/files/{file_id_with_ext}",  # UUID 文件下载（分享链接场景）
    "/api/v1/settings/config",  # 仅返回版本/时区等无害配置信息
}


def _build_app() -> FastAPI:
    """组装包含全部业务路由的应用，服务依赖统一替换为 mock。

    鉴权在依赖解析阶段触发，handler 不会真正执行，
    因此 mock 服务无需配置具体行为。
    """
    from src.api import (
        manual_upload_routes,
        routes,
        settings_routes,
        stats_routes,
        video_info_routes,
        video_resource_routes,
    )

    test_settings = Settings(
        api_key=TEST_API_KEY,
        wecom_webhook_url="",
        debug=True,
        data_dir=Path(tempfile.mkdtemp()),
        dry_run=True,
    )

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(video_resource_routes.router)
    app.include_router(video_info_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(stats_routes.router)
    app.include_router(manual_upload_routes.router)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[routes.get_task_service] = lambda: AsyncMock()
    app.dependency_overrides[routes.get_file_service] = lambda: AsyncMock(
        **{"get_file.return_value": None}
    )
    app.dependency_overrides[video_resource_routes.get_file_service] = (
        lambda: AsyncMock()
    )
    app.dependency_overrides[video_info_routes.get_database] = lambda: AsyncMock()
    app.dependency_overrides[video_info_routes.get_downloader_manager] = (
        lambda: AsyncMock()
    )
    app.dependency_overrides[manual_upload_routes.get_manual_upload_service] = (
        lambda: AsyncMock()
    )
    return app


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return _build_app()


@pytest.fixture(scope="module")
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _iter_protected_routes(app: FastAPI):
    """枚举所有应受鉴权保护的 (method, url) 组合。"""
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in PUBLIC_PATHS:
            continue
        # 路径参数填充占位值
        url = re.sub(r"{[^}]+}", "placeholder", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            yield method, url, route.path


class TestAuthEnforcement:
    """全量路由鉴权扫描。"""

    def test_routes_are_registered(self, app: FastAPI) -> None:
        """确保扫描对象非空（防止路由组装失败导致测试空转）。"""
        protected = list(_iter_protected_routes(app))
        assert len(protected) >= 10, f"Only {len(protected)} routes found"

    def test_all_protected_routes_reject_missing_key(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """缺少 API key 时，所有受保护端点必须返回 401。"""
        failures = []
        for method, url, path in _iter_protected_routes(app):
            response = client.request(method, url)
            if response.status_code != status.HTTP_401_UNAUTHORIZED:
                failures.append(f"{method} {path} -> {response.status_code}")
        assert not failures, "Endpoints missing auth enforcement:\n" + "\n".join(
            failures
        )

    def test_all_protected_routes_reject_invalid_key(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """API key 错误时，所有受保护端点必须返回 403。"""
        failures = []
        for method, url, path in _iter_protected_routes(app):
            response = client.request(
                method, url, headers={"X-API-Key": "wrong-key"}
            )
            if response.status_code != status.HTTP_403_FORBIDDEN:
                failures.append(f"{method} {path} -> {response.status_code}")
        assert not failures, "Endpoints not rejecting invalid key:\n" + "\n".join(
            failures
        )


class TestPublicEndpoints:
    """显式公开的端点不应要求鉴权。"""

    def test_file_download_is_public(self, client: TestClient) -> None:
        """文件下载（UUID 保护）无需 key，未命中时返回 404 而非 401。"""
        response = client.get("/api/v1/files/00000000-0000-0000-0000-000000000000")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_config_is_public(self, client: TestClient) -> None:
        """系统配置端点公开，返回 200。"""
        response = client.get("/api/v1/settings/config")
        assert response.status_code == status.HTTP_200_OK
