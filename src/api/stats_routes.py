"""
统计分析 API 路由模块。

提供失败归因统计端点，用于回答"下载器成功率是多少""失败主要卡在哪个
error_code"这类运营问题，避免依赖翻查日志逐条统计。
"""

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import ApiKeyDep
from src.api.schemas import DownloadStatsResponse, ErrorResponse, ValidationErrorResponse
from src.db.database import Database, get_database
from src.utils.logger import logger


# Router for statistics endpoints
router = APIRouter(prefix="/api/v1/stats", tags=["Statistics"])


DatabaseDep = Annotated[Database, Depends(get_database)]


@router.get(
    "/downloads",
    response_model=DownloadStatsResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        422: {
            # 这个端点没有业务级 422（不像 POST /tasks 有 precheck 拒绝），
            # 422 只有一个触发源：days 越界/非整数未通过 FastAPI/pydantic
            # 校验，实际响应体是标准 RequestValidationError 结构
            # ``{"detail": [{loc, msg, type}, ...]}``——detail 是数组而不是
            # ErrorResponse 声明的字符串。这里直接复用 POST /tasks 同款的
            # 校验错误变体模型，不需要像 VideoNotDownloadableErrorResponse
            # 那样声明 Union/anyOf（外部 review 第 15 轮问题 2）。
            "model": ValidationErrorResponse,
            "description": "Invalid days parameter (out of range or not an integer)",
        },
        503: {"model": ErrorResponse, "description": "Database error, please try again later"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="下载失败归因统计",
    description=(
        "聚合最近 N 天的任务数据：状态分布、失败 error_code 分布、"
        "内容级（视频本身问题）/系统级（下载器/网络/风控等）失败拆分、"
        "音频/字幕下载器归属分布、按自然周（非严格 ISO 周）的完成/失败趋势。"
    ),
)
async def get_download_stats(
    _: ApiKeyDep,
    db: DatabaseDep,
    days: int = Query(30, ge=1, le=365, description="统计时间窗口（天数），1-365"),
) -> DownloadStatsResponse:
    """
    获取下载失败归因统计。

    数据完全来自 SQL GROUP BY 聚合查询（Database.get_download_stats），
    不在应用层遍历全表，避免任务量增长后端点变慢。
    """
    try:
        stats = await db.get_download_stats(days=days)
        logger.info(
            f"Download stats queried: days={days}, total={stats['total']}"
        )
        return DownloadStatsResponse(days=days, **stats)

    except aiosqlite.Error as e:
        logger.error(f"Database error during download stats query: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error, please try again later",
        )
    except Exception as e:
        logger.error(f"Unexpected error during download stats query: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
