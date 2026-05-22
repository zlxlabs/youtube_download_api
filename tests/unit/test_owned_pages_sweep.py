"""M1: _owned_pages 在读路径前要 sweep 掉 dead pages。

背景：
- _owned_pages 是 set of Playwright Page；
- 外部因素（Chrome 崩溃、网络断开）可让 Page 变为已关闭状态；
- 原实现的列表推导 `[p for p in self._owned_pages if not p.is_closed()]`
  在 p.is_closed() 自身抛异常时会让整个清理流程失败；
- 长期运行下，已关闭/失效的 Page 引用累积。

修复：
- _safe_is_closed 容错地判断；
- 在所有 _owned_pages 读路径前 sweep 集合，剔除已关闭与失效引用。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.downloaders.cdp.human_behavior import HumanBehaviorSimulator


@pytest.fixture
def simulator() -> HumanBehaviorSimulator:
    return HumanBehaviorSimulator(Settings(api_key="test-key"))


class FakeLivePage:
    def is_closed(self) -> bool:
        return False


class FakeClosedPage:
    def is_closed(self) -> bool:
        return True


class FakeBrokenPage:
    def is_closed(self) -> bool:
        raise ConnectionError("CDP session lost")


def test_safe_is_closed_helper_exists(simulator: HumanBehaviorSimulator):
    """_safe_is_closed 必须作为公开（内部）helper 存在。"""
    assert hasattr(simulator, "_safe_is_closed"), (
        "需要 _safe_is_closed 容错判断方法"
    )


def test_safe_is_closed_returns_true_for_closed(simulator: HumanBehaviorSimulator):
    assert simulator._safe_is_closed(FakeClosedPage()) is True


def test_safe_is_closed_returns_false_for_live(simulator: HumanBehaviorSimulator):
    assert simulator._safe_is_closed(FakeLivePage()) is False


def test_safe_is_closed_treats_broken_as_closed(simulator: HumanBehaviorSimulator):
    """is_closed() 自身抛异常时，视为已关闭（避免阻塞清理）。"""
    assert simulator._safe_is_closed(FakeBrokenPage()) is True


def test_sweep_dead_pages_removes_closed(simulator: HumanBehaviorSimulator):
    """_sweep_dead_pages 必须存在，调用后已关闭/失效的 Page 被剔除。"""
    assert hasattr(simulator, "_sweep_dead_pages"), (
        "需要 _sweep_dead_pages 在读路径前调用"
    )

    live = FakeLivePage()
    closed = FakeClosedPage()
    broken = FakeBrokenPage()
    simulator._owned_pages.update({live, closed, broken})

    simulator._sweep_dead_pages()

    assert live in simulator._owned_pages
    assert closed not in simulator._owned_pages
    assert broken not in simulator._owned_pages
    assert len(simulator._owned_pages) == 1


def test_sweep_dead_pages_is_idempotent(simulator: HumanBehaviorSimulator):
    """对全是 live 的集合连续 sweep 不应改变内容。"""
    live1 = FakeLivePage()
    live2 = FakeLivePage()
    simulator._owned_pages.update({live1, live2})

    simulator._sweep_dead_pages()
    simulator._sweep_dead_pages()

    assert simulator._owned_pages == {live1, live2}
