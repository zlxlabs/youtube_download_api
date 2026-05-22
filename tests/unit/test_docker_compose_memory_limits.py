"""L3: 生产 docker-compose 必须配置内存上限和禁 swap，避免泄漏蔓延宿主机。"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:
    pytest.skip("PyYAML 未安装", allow_module_level=True)


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker" / "docker-compose.prod.yml"


@pytest.fixture(scope="module")
def compose_config() -> dict:
    assert COMPOSE_PATH.exists(), f"找不到 {COMPOSE_PATH}"
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_youtube_api_has_mem_limit(compose_config: dict):
    svc = compose_config["services"]["youtube-api"]
    assert "mem_limit" in svc, "youtube-api 必须配置 mem_limit"


def test_youtube_api_has_memswap_limit_equal_to_mem(compose_config: dict):
    """memswap_limit == mem_limit 即禁用 swap，确保泄漏时快速 OOMKill。"""
    svc = compose_config["services"]["youtube-api"]
    assert "memswap_limit" in svc, "必须显式配置 memswap_limit"
    assert svc["memswap_limit"] == svc["mem_limit"], (
        "memswap_limit 应等于 mem_limit 以禁用 swap，"
        f"当前 mem_limit={svc['mem_limit']} memswap_limit={svc['memswap_limit']}"
    )


def test_youtube_api_restart_policy(compose_config: dict):
    """OOMKill 后必须自动拉起。"""
    svc = compose_config["services"]["youtube-api"]
    assert svc.get("restart") in ("unless-stopped", "always"), (
        f"restart 策略应为 unless-stopped 或 always，当前为 {svc.get('restart')}"
    )
