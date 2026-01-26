"""
测试下载器重试机制。

演示新的重试逻辑：最多重试 3 次，指数退避 (1s, 2s, 4s)。
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 设置标准输出编码为 UTF-8（Windows）
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")

# 设置环境变量
os.environ["ENV_FILE"] = ".env.development"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.manager import DownloaderManager
from src.downloaders.models import DownloaderResult, VideoMetadata


class MockDownloader:
    """模拟下载器，用于测试重试机制。"""

    def __init__(self, name: str, fail_times: int = 0, should_retry_value: bool = True):
        """
        初始化模拟下载器。

        Args:
            name: 下载器名称
            fail_times: 失败次数（前 N 次调用会失败）
            should_retry_value: should_retry() 的返回值
        """
        self.name = name
        self.fail_times = fail_times
        self.should_retry_value = should_retry_value
        self.call_count = 0
        self.is_available = True

    async def download(self, video_url: str, video_id: str, output_dir: Path, **kwargs):
        """模拟下载方法。"""
        self.call_count += 1
        print(f"  [{self.name}] Download attempt {self.call_count}")

        if self.call_count <= self.fail_times:
            print(f"  [{self.name}] Simulating failure...")
            raise DownloaderError(
                message="Simulated network error",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            )

        # 成功
        print(f"  [{self.name}] Success!")
        return DownloaderResult(
            success=True,
            downloader=self.name,
            video_metadata=VideoMetadata(
                video_id=video_id,
                title="Test Video",
                author="Test Author",
                duration=100,
            ),
            audio_path=None,
            transcript_path=None,
            has_transcript=False,
        )

    def should_retry(self, error: Exception) -> bool:
        """是否应该重试。"""
        return self.should_retry_value

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """是否触发熔断器。"""
        return False


async def test_scenario_1_success_first_try():
    """场景 1：第一次就成功（无重试）。"""
    print("\n" + "=" * 60)
    print("场景 1: 第一次尝试就成功")
    print("=" * 60)

    mock_downloader = MockDownloader(name="mock", fail_times=0)

    manager = DownloaderManager.__new__(DownloaderManager)
    manager.downloaders = [mock_downloader]
    manager.circuit_breakers = {}
    manager.stats = MagicMock()
    manager.stats.record_success = MagicMock()

    result = await manager._download_with_downloader(
        downloader=mock_downloader,
        video_url="https://example.com/video",
        video_id="test123",
        output_dir=Path("/tmp"),
        include_audio=True,
        include_transcript=False,
    )

    print(f"\n结果:")
    print(f"  调用次数: {mock_downloader.call_count}")
    print(f"  预期: 1 次（无重试）")
    print(f"  状态: {'[PASS]' if mock_downloader.call_count == 1 else '[FAIL]'}")


async def test_scenario_2_success_after_retries():
    """场景 2：第 2 次重试成功（共 3 次调用）。"""
    print("\n" + "=" * 60)
    print("场景 2: 第 2 次重试成功（失败 -> 失败 -> 成功）")
    print("=" * 60)

    mock_downloader = MockDownloader(name="mock", fail_times=2, should_retry_value=True)

    manager = DownloaderManager.__new__(DownloaderManager)
    manager.downloaders = [mock_downloader]
    manager.circuit_breakers = {}
    manager.stats = MagicMock()

    print("\n预期: 3 次调用，退避时间 1s + 2s = 3s")
    print("开始测试...\n")

    import time
    start_time = time.time()

    result = await manager._download_with_downloader(
        downloader=mock_downloader,
        video_url="https://example.com/video",
        video_id="test123",
        output_dir=Path("/tmp"),
        include_audio=True,
        include_transcript=False,
    )

    elapsed = time.time() - start_time

    print(f"\n结果:")
    print(f"  调用次数: {mock_downloader.call_count}")
    print(f"  预期: 3 次")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  预期耗时: ~3s (1s + 2s)")
    print(f"  状态: {'[PASS]' if mock_downloader.call_count == 3 and 2.5 < elapsed < 4 else '[FAIL]'}")


async def test_scenario_3_max_retries_reached():
    """场景 3：达到最大重试次数（失败 4 次）。"""
    print("\n" + "=" * 60)
    print("场景 3: 达到最大重试次数")
    print("=" * 60)

    mock_downloader = MockDownloader(name="mock", fail_times=10, should_retry_value=True)

    manager = DownloaderManager.__new__(DownloaderManager)
    manager.downloaders = [mock_downloader]
    manager.circuit_breakers = {}
    manager.stats = MagicMock()

    print("\n预期: 4 次调用，退避时间 1s + 2s + 4s = 7s")
    print("开始测试...\n")

    import time
    start_time = time.time()

    try:
        result = await manager._download_with_downloader(
            downloader=mock_downloader,
            video_url="https://example.com/video",
            video_id="test123",
            output_dir=Path("/tmp"),
            include_audio=True,
            include_transcript=False,
        )
        print("\n[FAIL] 应该抛出异常")
    except DownloaderError as e:
        elapsed = time.time() - start_time
        print(f"\n结果:")
        print(f"  调用次数: {mock_downloader.call_count}")
        print(f"  预期: 4 次 (1 次原始 + 3 次重试)")
        print(f"  耗时: {elapsed:.1f}s")
        print(f"  预期耗时: ~7s (1s + 2s + 4s)")
        print(f"  异常类型: {type(e).__name__}")
        print(f"  状态: {'[PASS]' if mock_downloader.call_count == 4 and 6 < elapsed < 9 else '[FAIL]'}")


async def test_scenario_4_non_retryable_error():
    """场景 4：不可重试的错误（立即失败）。"""
    print("\n" + "=" * 60)
    print("场景 4: 不可重试的错误（如 API 限流）")
    print("=" * 60)

    mock_downloader = MockDownloader(
        name="mock", fail_times=10, should_retry_value=False  # should_retry 返回 False
    )

    manager = DownloaderManager.__new__(DownloaderManager)
    manager.downloaders = [mock_downloader]
    manager.circuit_breakers = {}
    manager.stats = MagicMock()

    print("\n预期: 1 次调用，无重试，立即失败")
    print("开始测试...\n")

    try:
        result = await manager._download_with_downloader(
            downloader=mock_downloader,
            video_url="https://example.com/video",
            video_id="test123",
            output_dir=Path("/tmp"),
            include_audio=True,
            include_transcript=False,
        )
        print("\n[FAIL] 应该抛出异常")
    except DownloaderError as e:
        print(f"\n结果:")
        print(f"  调用次数: {mock_downloader.call_count}")
        print(f"  预期: 1 次（无重试）")
        print(f"  异常类型: {type(e).__name__}")
        print(f"  状态: {'[PASS]' if mock_downloader.call_count == 1 else '[FAIL]'}")


async def main():
    """运行所有测试场景。"""
    print("=" * 60)
    print("下载器重试机制测试")
    print("=" * 60)
    print("\n配置:")
    print("  最大重试次数: 3")
    print("  退避策略: 指数退避 (1s, 2s, 4s)")
    print("  总尝试次数: 4 (1次原始 + 3次重试)")

    await test_scenario_1_success_first_try()
    await test_scenario_2_success_after_retries()
    await test_scenario_3_max_retries_reached()
    await test_scenario_4_non_retryable_error()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
