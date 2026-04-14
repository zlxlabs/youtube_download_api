"""
CDN 节点切换测试。

当 curl-cffi 请求 googlevideo.com 直链返回 403 时，
解析 URL 中的 mn 参数，切换备用 CDN 节点重试。

YouTube CDN URL 格式：
  https://rr{N}---{node}.googlevideo.com/videoplayback?..&mn={node1},{node2}&..
  - mn 参数列出所有可用节点
  - URL 签名（sig/lsig）不绑定 hostname，可安全替换
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError


# ── 真实 URL 样本（已脱敏） ──────────────────────────────────────────
SAMPLE_URL_TWO_NODES = (
    "https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback"
    "?expire=1776164205&ei=abc&ip=1.2.3.4&itag=140"
    "&mn=sn-npoe7ndl%2Csn-npoldn7z"
    "&mm=31%2C29&ms=au%2Crdu&mv=u&mvi=1"
    "&sig=ABCDEF&lsig=XYZ"
)

SAMPLE_URL_ONE_NODE = (
    "https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback"
    "?expire=1776164205&itag=140"
    "&mn=sn-npoe7ndl"
    "&sig=ABCDEF"
)

SAMPLE_URL_NO_MN = (
    "https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback"
    "?expire=1776164205&itag=140&sig=ABCDEF"
)

SAMPLE_URL_THREE_NODES = (
    "https://rr2---sn-aaa.googlevideo.com/videoplayback"
    "?expire=1776164205&itag=140"
    "&mn=sn-aaa%2Csn-bbb%2Csn-ccc"
    "&sig=ABCDEF"
)


# ── _get_cdn_fallback_urls 单元测试 ──────────────────────────────────

class TestGetCdnFallbackUrls:
    """测试 CDN 节点备用 URL 生成逻辑。"""

    def _get_fn(self):
        """获取待测函数。"""
        from src.downloaders.cdp.audio_downloader import AudioDownloader
        return AudioDownloader._get_cdn_fallback_urls

    def test_two_nodes_returns_one_alternative(self):
        """mn 有两个节点时，返回切换到备用节点的 URL。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_TWO_NODES)

        assert len(result) == 1
        assert "sn-npoldn7z" in result[0]
        assert "sn-npoe7ndl" not in result[0].split("googlevideo.com")[0]

    def test_alternative_url_preserves_all_query_params(self):
        """切换节点后，其余查询参数保持不变。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_TWO_NODES)

        assert len(result) == 1
        alt_url = result[0]
        assert "expire=1776164205" in alt_url
        assert "itag=140" in alt_url
        assert "sig=ABCDEF" in alt_url
        assert "lsig=XYZ" in alt_url

    def test_alternative_url_preserves_rr_prefix(self):
        """切换节点时保留 rr{N} 编号。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_TWO_NODES)

        assert result[0].startswith("https://rr1---sn-npoldn7z.googlevideo.com")

    def test_single_node_returns_empty(self):
        """mn 只有一个节点时，返回空列表。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_ONE_NODE)

        assert result == []

    def test_no_mn_param_returns_empty(self):
        """URL 没有 mn 参数时，返回空列表。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_NO_MN)

        assert result == []

    def test_three_nodes_returns_two_alternatives(self):
        """mn 有三个节点时，返回两个备用 URL。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_THREE_NODES)

        assert len(result) == 2
        hosts = [u.split("/")[2] for u in result]
        assert any("sn-bbb" in h for h in hosts)
        assert any("sn-ccc" in h for h in hosts)

    def test_rr_prefix_from_different_rr_number(self):
        """rr2 前缀也能正确保留。"""
        fn = self._get_fn()
        result = fn(SAMPLE_URL_THREE_NODES)

        assert all(u.startswith("https://rr2---") for u in result)

    def test_non_googlevideo_url_returns_empty(self):
        """非 googlevideo URL 不做处理，返回空列表。"""
        fn = self._get_fn()
        result = fn("https://example.com/file.mp4?mn=node1%2Cnode2")

        assert result == []


# ── download_audio CDN 节点切换集成测试 ──────────────────────────────

@pytest.fixture
def audio_downloader():
    """创建 AudioDownloader 实例（mock 所有外部依赖）。"""
    from src.downloaders.cdp.audio_downloader import AudioDownloader

    settings = MagicMock()
    settings.cdp_use_curl_cffi = True
    settings.cdp_enable_multipart = True
    settings.cdp_multipart_min_size = 1
    settings.data_dir = Path("/tmp/test_cdn")
    settings.cdp_convert_to_m4a = False

    return AudioDownloader(settings=settings, downloader_name="cdp")


@pytest.fixture
def audio_info():
    """构造音频信息对象。"""
    from src.downloaders.cdp.models import AudioInfo
    return AudioInfo(
        url=SAMPLE_URL_TWO_NODES,
        title="test_video",
        ext="m4a",
        mime_type="audio/mp4",
        itag=140,
        filesize=10 * 1024 * 1024,  # 10MB，触发分片下载
    )


@pytest.mark.asyncio
async def test_cdn_fallback_succeeds_after_403(audio_downloader, audio_info, tmp_path):
    """
    场景：分片和单线程均 403，切换 CDN 节点后成功。
    期望：返回文件路径，不抛出异常。
    """
    err_403 = DownloaderError(
        message="HTTP 403 for chunk 0",
        error_code=ErrorCode.CDP_DOWNLOAD_403,
        downloader="cdp",
    )

    call_count = {"n": 0}

    async def mock_curl_multipart(url, target_path, expected_size, headers):
        raise err_403

    async def mock_curl_single(url, target_path, expected_size, headers):
        call_count["n"] += 1
        # 检查 hostname，避免 mn 参数中的节点名干扰判断
        from urllib.parse import urlparse as _up
        if _up(url).hostname and "sn-npoe7ndl" in _up(url).hostname:
            raise err_403
        # 备用节点成功：写入假文件
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_curl_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_curl_single),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={},
        )

    assert result is not None
    assert result.exists()
    # 第一次（原始节点 403）+ 第二次（备用节点成功）
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_cdn_fallback_all_nodes_fail_then_ytdlp(audio_downloader, audio_info, tmp_path):
    """
    场景：所有 CDN 节点均 403，最终走 yt-dlp 兜底。
    期望：yt-dlp 被调用。
    """
    err_403 = DownloaderError(
        message="HTTP 403",
        error_code=ErrorCode.CDP_DOWNLOAD_403,
        downloader="cdp",
    )

    ytdlp_called = {"called": False}
    fake_file = tmp_path / "fake.m4a"

    async def mock_curl_multipart(url, target_path, expected_size, headers):
        raise err_403

    async def mock_curl_single(url, target_path, expected_size, headers):
        raise err_403

    async def mock_ytdlp(video_url, cookie_file, output_dir, expected_filename):
        ytdlp_called["called"] = True
        fake_file.write_bytes(b"ytdlp audio")
        return fake_file

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_curl_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_curl_single),
        patch.object(audio_downloader, "_download_with_ytdlp", mock_ytdlp),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={},
        )

    assert ytdlp_called["called"] is True
    assert result == fake_file


@pytest.mark.asyncio
async def test_no_cdn_fallback_when_url_has_single_node(audio_downloader, tmp_path):
    """
    场景：URL 中只有一个 CDN 节点，403 后直接走 yt-dlp，不浪费重试次数。
    """
    from src.downloaders.cdp.models import AudioInfo

    single_node_info = AudioInfo(
        url=SAMPLE_URL_ONE_NODE,
        title="test_video",
        ext="m4a",
        mime_type="audio/mp4",
        itag=140,
        filesize=10 * 1024 * 1024,
    )

    err_403 = DownloaderError(
        message="HTTP 403",
        error_code=ErrorCode.CDP_DOWNLOAD_403,
        downloader="cdp",
    )
    fake_file = tmp_path / "fake.m4a"
    cdn_retry_urls = []

    async def mock_curl_multipart(url, target_path, expected_size, headers):
        raise err_403

    async def mock_curl_single(url, target_path, expected_size, headers):
        cdn_retry_urls.append(url)
        raise err_403

    async def mock_ytdlp(video_url, cookie_file, output_dir, expected_filename):
        fake_file.write_bytes(b"ytdlp audio")
        return fake_file

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_curl_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_curl_single),
        patch.object(audio_downloader, "_download_with_ytdlp", mock_ytdlp),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        await audio_downloader.download_audio(
            audio_info=single_node_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={},
        )

    # 只有一个节点，不应该有 CDN 切换重试
    assert len(cdn_retry_urls) == 1
