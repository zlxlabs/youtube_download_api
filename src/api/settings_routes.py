"""
设置管理 API 路由模块。

提供 Cookie 文件的查看、编辑、验证等管理功能。
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import ApiKeyDep
from src.api.schemas import ErrorResponse
from src.config import get_settings
from src.utils.logger import logger
import src


# Router for settings management
router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])


# ==================== Request/Response Models ====================


class CookieInfoResponse(BaseModel):
    """Cookie 信息响应模型。"""

    path: str = Field(description="Cookie 文件路径")
    exists: bool = Field(description="文件是否存在")
    size: Optional[int] = Field(None, description="文件大小（字节）")
    last_modified: Optional[str] = Field(None, description="最后修改时间（ISO 8601）")
    content: Optional[str] = Field(None, description="Cookie 文件内容")


class UpdateCookieRequest(BaseModel):
    """更新 Cookie 请求模型。"""

    content: str = Field(description="Cookie 文件内容")
    create_backup: bool = Field(default=True, description="是否创建备份")


class ValidateCookieRequest(BaseModel):
    """验证 Cookie 请求模型。"""

    content: str = Field(description="要验证的 Cookie 内容")


class ValidateCookieResponse(BaseModel):
    """验证 Cookie 响应模型。"""

    valid: bool = Field(description="是否有效")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    warnings: list[str] = Field(default_factory=list, description="警告列表")
    line_count: int = Field(description="有效行数")


# ==================== Helper Functions ====================


def get_cookie_file_path() -> Path:
    """
    获取 Cookie 文件路径。

    Returns:
        Cookie 文件路径
    """
    settings = get_settings()
    if settings.cookie_file:
        return Path(settings.cookie_file)
    # 默认路径
    return Path("cookies.txt")


def validate_cookie_content(content: str) -> ValidateCookieResponse:
    """
    验证 Cookie 内容格式。

    Args:
        content: Cookie 文件内容

    Returns:
        验证结果
    """
    errors = []
    warnings = []
    valid_lines = 0

    lines = content.strip().split("\n")

    # 检查 Netscape header
    if not lines or not lines[0].strip().startswith("# Netscape HTTP Cookie File"):
        warnings.append("缺少 Netscape HTTP Cookie File header")

    # 验证每一行
    has_youtube_domain = False
    for i, line in enumerate(lines, 1):
        line = line.strip()

        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue

        # 检查字段数量（应该是 7 个 tab 分隔的字段）
        fields = line.split("\t")
        if len(fields) != 7:
            errors.append(f"第 {i} 行：字段数量错误（期望 7 个，实际 {len(fields)} 个）")
            continue

        domain, flag, path, secure, expiration, name, value = fields

        # 验证域名
        if not domain:
            errors.append(f"第 {i} 行：域名为空")
        elif ".youtube.com" in domain:
            has_youtube_domain = True

        # 验证过期时间（应该是 Unix 时间戳）
        if not re.match(r"^\d+$", expiration):
            errors.append(f"第 {i} 行：过期时间格式错误（应为 Unix 时间戳）")

        valid_lines += 1

    # 检查是否包含 YouTube 域名
    if not has_youtube_domain:
        warnings.append("未找到 .youtube.com 域名的 Cookie")

    is_valid = len(errors) == 0 and valid_lines > 0

    return ValidateCookieResponse(
        valid=is_valid,
        errors=errors,
        warnings=warnings,
        line_count=valid_lines,
    )


def create_cookie_backup(cookie_path: Path) -> Optional[Path]:
    """
    创建 Cookie 文件备份。

    Args:
        cookie_path: Cookie 文件路径

    Returns:
        备份文件路径，失败返回 None
    """
    if not cookie_path.exists():
        return None

    try:
        # 生成备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = cookie_path.with_suffix(f".txt.backup.{timestamp}")

        # 复制文件
        backup_path.write_bytes(cookie_path.read_bytes())

        # 在 Unix 系统上设置文件权限为 600
        if os.name != "nt":
            os.chmod(backup_path, 0o600)

        logger.info(f"Created cookie backup: {backup_path}")

        # 清理旧备份（保留最近 10 个）
        cleanup_old_backups(cookie_path)

        return backup_path

    except Exception as e:
        logger.error(f"Failed to create cookie backup: {e}")
        return None


def cleanup_old_backups(cookie_path: Path, keep: int = 10) -> None:
    """
    清理旧的备份文件。

    Args:
        cookie_path: Cookie 文件路径
        keep: 保留的备份数量
    """
    try:
        # 查找所有备份文件
        backup_pattern = f"{cookie_path.stem}.txt.backup.*"
        backups = sorted(
            cookie_path.parent.glob(backup_pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        # 删除超出保留数量的备份
        for backup in backups[keep:]:
            backup.unlink()
            logger.debug(f"Deleted old cookie backup: {backup}")

    except Exception as e:
        logger.error(f"Failed to cleanup old backups: {e}")


# ==================== Settings Endpoints ====================


class ConfigResponse(BaseModel):
    """系统配置响应模型。"""

    timezone: str = Field(description="系统时区配置")
    debug: bool = Field(description="调试模式")
    file_retention_days: int = Field(description="文件保留天数")
    version: str = Field(description="系统版本号")
    build_time: str = Field(description="构建时间（ISO 8601 格式）")


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="获取系统配置",
    description="获取系统配置信息（公开接口，无需鉴权）。",
)
async def get_config() -> ConfigResponse:
    """
    获取系统配置信息。

    返回时区、调试模式、文件保留期、版本号、构建时间等配置。
    """
    settings = get_settings()

    # 获取构建时间，如果是占位符则返回 "development"
    build_time = src.__build_time__
    if "PLACEHOLDER" in build_time:
        build_time = "development"

    return ConfigResponse(
        timezone=settings.tz,
        debug=settings.debug,
        file_retention_days=settings.file_retention_days,
        version=src.__version__,
        build_time=build_time,
    )


# ==================== Cookie Endpoints ====================


@router.get(
    "/cookie",
    response_model=CookieInfoResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="获取 Cookie 信息",
    description="获取 Cookie 文件的信息和内容。",
)
async def get_cookie_info(_: ApiKeyDep) -> CookieInfoResponse:
    """
    获取 Cookie 文件信息。

    包括文件路径、是否存在、大小、最后修改时间、内容。
    """
    try:
        cookie_path = get_cookie_file_path()

        if not cookie_path.exists():
            return CookieInfoResponse(
                path=str(cookie_path),
                exists=False,
            )

        # 读取文件信息
        stat = cookie_path.stat()
        content = cookie_path.read_text(encoding="utf-8")

        logger.info(f"Fetched cookie info: {cookie_path}")

        return CookieInfoResponse(
            path=str(cookie_path),
            exists=True,
            size=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            content=content,
        )

    except Exception as e:
        logger.error(f"Failed to get cookie info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read cookie file: {str(e)}",
        )


@router.put(
    "/cookie",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        400: {"model": ErrorResponse, "description": "Invalid cookie format"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="更新 Cookie",
    description="更新 Cookie 文件内容，可选自动创建备份。",
)
async def update_cookie(
    request: UpdateCookieRequest,
    _: ApiKeyDep,
) -> dict[str, Any]:
    """
    更新 Cookie 文件。

    自动验证内容格式，可选创建备份。
    """
    try:
        # 验证内容格式
        validation = validate_cookie_content(request.content)
        if not validation.valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Invalid cookie format",
                    "errors": validation.errors,
                    "warnings": validation.warnings,
                },
            )

        cookie_path = get_cookie_file_path()

        # 创建备份
        backup_path = None
        if request.create_backup and cookie_path.exists():
            backup_path = create_cookie_backup(cookie_path)

        # 确保父目录存在
        cookie_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入新内容
        cookie_path.write_text(request.content, encoding="utf-8")

        # 在 Unix 系统上设置文件权限为 600
        if os.name != "nt":
            os.chmod(cookie_path, 0o600)

        logger.info(f"Updated cookie file: {cookie_path}")

        return {
            "message": "Cookie updated successfully",
            "path": str(cookie_path),
            "backup_path": str(backup_path) if backup_path else None,
            "warnings": validation.warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cookie: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write cookie file: {str(e)}",
        )


@router.post(
    "/cookie/validate",
    response_model=ValidateCookieResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
    },
    summary="验证 Cookie 格式",
    description="验证 Cookie 内容的格式是否正确。",
)
async def validate_cookie(
    request: ValidateCookieRequest,
    _: ApiKeyDep,
) -> ValidateCookieResponse:
    """
    验证 Cookie 格式。

    检查 Netscape 格式、字段数量、域名等。
    """
    return validate_cookie_content(request.content)
