"""
下载失败归因统计端点测试（GET /api/v1/stats/downloads）。

覆盖：鉴权、days 参数边界校验、响应字段组装正确性。
聚合 SQL 本身（GROUP BY 各维度是否正确）已在
tests/unit/test_database.py::TestDownloadStatsAggregation 用真实数据库 +
种子数据覆盖；本文件用 mock Database 聚焦端点层的鉴权/校验/序列化。
"""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from src.config import Settings, get_settings

TEST_API_KEY = "test-api-key-12345"


def _auth_headers(api_key: str = TEST_API_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _sample_stats(total: int = 10) -> dict[str, Any]:
    """构造一份符合 Database.get_download_stats 返回结构的样例数据。"""
    return {
        "total": total,
        "by_status": {"completed": 6, "failed": 3, "pending": 1},
        "failures_by_error_code": {"VIDEO_PRIVATE": 1, "CDP_NO_COOKIES": 2},
        "failure_split": {
            "content_level": 1,
            "system_level": 2,
            "content_level_ratio": 1 / 3,
            "system_level_ratio": 2 / 3,
        },
        "by_downloader": {
            "audio_downloader": {"cdp": 4, "unknown": 2},
            "transcript_downloader": {"ytdlp": 5, "unknown": 1},
        },
        "weekly_trend": [
            {"week": "2026-W27", "completed": 3, "failed": 1},
            {"week": "2026-W28", "completed": 3, "failed": 2},
        ],
    }


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock Database，get_download_stats 返回样例聚合数据。"""
    db = AsyncMock()
    db.get_download_stats = AsyncMock(return_value=_sample_stats())
    return db


@pytest.fixture
def client(mock_db: AsyncMock):
    """构造只挂载 stats_routes 的最小 FastAPI app，Database 依赖用 mock 注入。"""
    from src.api import stats_routes

    test_settings = Settings(
        api_key=TEST_API_KEY,
        wecom_webhook_url="",
        debug=True,
        data_dir=Path(tempfile.mkdtemp()),
        dry_run=True,
    )

    app = FastAPI()
    app.include_router(stats_routes.router)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[stats_routes.get_database] = lambda: mock_db

    yield TestClient(app)

    app.dependency_overrides.clear()


# ==================== Authentication ====================


class TestStatsAuthentication:
    def test_missing_api_key_returns_401(self, client: TestClient) -> None:
        response = client.get("/api/v1/stats/downloads")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_api_key_returns_403(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/stats/downloads", headers=_auth_headers("wrong-key")
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ==================== days 参数校验 ====================


class TestDaysParameterValidation:
    def test_default_days_is_30(self, client: TestClient, mock_db: AsyncMock) -> None:
        response = client.get("/api/v1/stats/downloads", headers=_auth_headers())
        assert response.status_code == status.HTTP_200_OK
        mock_db.get_download_stats.assert_awaited_once_with(days=30)

    def test_custom_days_passed_through(self, client: TestClient, mock_db: AsyncMock) -> None:
        response = client.get(
            "/api/v1/stats/downloads?days=7", headers=_auth_headers()
        )
        assert response.status_code == status.HTTP_200_OK
        mock_db.get_download_stats.assert_awaited_once_with(days=7)

    @pytest.mark.parametrize("days", [0, -1, 366, 1000])
    def test_out_of_range_days_returns_422(self, client: TestClient, days: int) -> None:
        response = client.get(
            f"/api/v1/stats/downloads?days={days}", headers=_auth_headers()
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_boundary_days_accepted(self, client: TestClient, days: int) -> None:
        response = client.get(
            f"/api/v1/stats/downloads?days={days}", headers=_auth_headers()
        )
        assert response.status_code == status.HTTP_200_OK


# ==================== 响应字段组装 ====================


class TestDownloadStatsResponseShape:
    def test_response_contains_all_aggregation_fields(
        self, client: TestClient, mock_db: AsyncMock
    ) -> None:
        response = client.get("/api/v1/stats/downloads", headers=_auth_headers())
        assert response.status_code == status.HTTP_200_OK
        body = response.json()

        assert body["days"] == 30
        assert body["total"] == 10
        assert body["by_status"] == {"completed": 6, "failed": 3, "pending": 1}
        assert body["failures_by_error_code"] == {
            "VIDEO_PRIVATE": 1,
            "CDP_NO_COOKIES": 2,
        }
        assert body["failure_split"]["content_level"] == 1
        assert body["failure_split"]["system_level"] == 2
        assert body["by_downloader"]["audio_downloader"]["cdp"] == 4
        assert body["by_downloader"]["audio_downloader"]["unknown"] == 2
        assert body["by_downloader"]["transcript_downloader"]["ytdlp"] == 5
        assert len(body["weekly_trend"]) == 2
        assert body["weekly_trend"][0] == {
            "week": "2026-W27",
            "completed": 3,
            "failed": 1,
        }


# ==================== OpenAPI 声明 ====================


class TestStatsOpenApiSchema:
    def test_openapi_declares_503_for_database_error(self, client: TestClient) -> None:
        """
        GET /api/v1/stats/downloads 的实现在 aiosqlite.Error 时返回 503
        （见 stats_routes.py），OpenAPI responses 声明必须包含 503，否则
        据此生成的客户端代码/文档会漏掉这种真实会发生的响应状态码
        （外部 review 第 4 轮问题 2）。
        """
        schema = client.app.openapi()  # type: ignore[union-attr]
        responses = schema["paths"]["/api/v1/stats/downloads"]["get"]["responses"]

        assert "503" in responses
        ref = responses["503"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.rsplit("/", 1)[-1] == "ErrorResponse"

    def test_openapi_422_schema_matches_actual_validation_error_shape(
        self, client: TestClient
    ) -> None:
        """
        外部 review 第15轮问题2(P2)：GET /api/v1/stats/downloads 的 422 只有一个
        触发源——days 越界/非整数未通过 FastAPI/pydantic 校验，实际响应体是标准
        RequestValidationError 结构 ``{"detail": [{loc, msg, type}, ...]}``（见
        test_out_of_range_days_returns_422），detail 是数组而不是字符串。

        这个端点没有像 POST /tasks 那样的业务 422（precheck 拒绝），所以不需要
        像 VideoNotDownloadableErrorResponse 那样声明 Union/anyOf，直接复用
        POST /tasks 同款校验错误变体模型 ValidationErrorResponse
        （detail: list[ValidationErrorDetail]）即可——此前误声明成了 ErrorResponse
        （detail: str），与实际响应体形态不符。
        """
        schema = client.app.openapi()  # type: ignore[union-attr]
        responses = schema["paths"]["/api/v1/stats/downloads"]["get"]["responses"]

        assert "422" in responses
        ref = responses["422"]["content"]["application/json"]["schema"]["$ref"]
        model_name = ref.rsplit("/", 1)[-1]

        assert model_name == "ValidationErrorResponse"

        model_schema = schema["components"]["schemas"][model_name]
        detail_schema = model_schema["properties"]["detail"]
        assert detail_schema["type"] == "array"

        item_ref = detail_schema["items"]["$ref"]
        assert item_ref.rsplit("/", 1)[-1] == "ValidationErrorDetail"
