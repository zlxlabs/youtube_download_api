"""
curl-cffi 403 阶段级 Cookie 重试测试。

背景（生产取证结论）：
- CDP 下载器通过 Network.getAllCookies 拿到完整 cookie 列表，
  但此前只写成 Netscape 文件给 yt-dlp 用，从未合并进 curl-cffi 下载请求。
- 生产日志显示 curl-cffi 无 cookie 请求在 97% 的日子畅通，
  但 YouTube 间歇性收紧校验时会爆发 403（32 天 60 次诊断，100% has_cookie=False）。
- 设计决策：首次请求保持无 cookie（最保守的账号风控策略），
  仅在收到 403 后做一次"带 cookie 重试"，仍失败才走现有降级链
  （单线程 → CDN 切换 → yt-dlp 兜底）。

本文件覆盖两部分：
1. 域匹配纯函数 AudioDownloader._build_cookie_header（RFC6265 简化版）
2. 分片 / 单线程 403 后的阶段级单次 cookie 重试集成行为
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import ErrorCode
from src.downloaders.cdp.audio_downloader import AudioDownloader
from src.downloaders.cdp.models import AudioInfo
from src.downloaders.exceptions import DownloaderError


# ── 真实场景样本（已脱敏） ───────────────────────────────────────────
# 分片/单线程下载都直接请求 googlevideo.com CDN 直链
TARGET_URL = (
    "https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback"
    "?expire=1776164205&itag=140&sig=ABCDEF"
)


def _err_403(context: str = "test") -> DownloaderError:
    """构造一个 403 DownloaderError，模拟 curl_cffi 请求被拒绝。"""
    return DownloaderError(
        message=f"HTTP 403 ({context})",
        error_code=ErrorCode.CDP_DOWNLOAD_403,
        downloader="cdp",
        http_status_code=403,
        stop_fallback=True,
    )


# ── 1. 域匹配纯函数测试：AudioDownloader._build_cookie_header ─────────

class TestBuildCookieHeader:
    """测试 cookie 列表 -> Cookie 请求头字符串的纯函数。"""

    def test_exact_domain_match(self):
        """cookie.domain 与目标 host 完全相等时应命中（host-only 或 domain cookie 均适用）。"""
        cookies = [
            {"name": "SID", "value": "abc123", "domain": "rr1---sn-npoe7ndl.googlevideo.com", "secure": False},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == "SID=abc123"

    def test_dot_prefix_suffix_match(self):
        """cookie.domain 以点开头（如 .googlevideo.com）时，host 后缀匹配应命中。"""
        cookies = [
            {"name": "HSID", "value": "xyz789", "domain": ".googlevideo.com", "secure": False},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == "HSID=xyz789"

    def test_host_only_domain_does_not_match_subdomain(self):
        """
        host-only cookie（RFC6265，domain 无前导点）不应做子域匹配。

        场景：cookie.domain="googlevideo.com"（无前导点，host-only），
        目标 URL host 是其子域 "rr1---sn-npoe7ndl.googlevideo.com"。
        按 RFC6265，浏览器本身也不会把这条 host-only cookie 发给该子域，
        因此这里同样不应匹配，否则会造成 cookie 作用域泄漏。
        """
        cookies = [
            {"name": "HOST_ONLY", "value": "leak", "domain": "googlevideo.com", "secure": False},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == ""

    def test_host_only_domain_matches_exact_host_only(self):
        """host-only cookie 的 domain 与 host 完全相等（无子域）时仍应命中。"""
        cookies = [
            {"name": "HOST_ONLY", "value": "ok", "domain": "googlevideo.com", "secure": False},
        ]
        exact_host_url = "https://googlevideo.com/videoplayback?itag=140"
        header = AudioDownloader._build_cookie_header(cookies, exact_host_url)
        assert header == "HOST_ONLY=ok"

    def test_domain_cookie_matches_root_domain_itself(self):
        """domain cookie（带前导点）应同时匹配根域名自身，不仅仅是子域。"""
        cookies = [
            {"name": "ROOT", "value": "ok", "domain": ".googlevideo.com", "secure": False},
        ]
        root_url = "https://googlevideo.com/videoplayback?itag=140"
        header = AudioDownloader._build_cookie_header(cookies, root_url)
        assert header == "ROOT=ok"

    def test_non_matching_domain_excluded(self):
        """domain 不匹配的 cookie 应被排除。"""
        cookies = [
            {"name": "unrelated", "value": "xxx", "domain": ".otherdomain.com", "secure": False},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == ""

    def test_secure_cookie_excluded_on_http(self):
        """secure cookie 仅用于 https，http 目标 URL 应排除该 cookie。"""
        cookies = [
            {"name": "SID", "value": "abc123", "domain": ".googlevideo.com", "secure": True},
        ]
        http_url = TARGET_URL.replace("https://", "http://")
        header = AudioDownloader._build_cookie_header(cookies, http_url)
        assert header == ""

    def test_secure_cookie_included_on_https(self):
        """secure cookie 在 https 目标 URL 下应正常命中。"""
        cookies = [
            {"name": "SID", "value": "abc123", "domain": ".googlevideo.com", "secure": True},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == "SID=abc123"

    def test_empty_cookie_list_returns_empty_string(self):
        """cookie 列表为空时返回空字符串。"""
        assert AudioDownloader._build_cookie_header([], TARGET_URL) == ""

    def test_none_cookie_list_returns_empty_string(self):
        """cookie 列表为 None 时返回空字符串（零行为变化保护）。"""
        assert AudioDownloader._build_cookie_header(None, TARGET_URL) == ""

    def test_multiple_matched_cookies_joined_with_semicolon(self):
        """多个命中的 cookie 应以 '; ' 拼接成单个 Cookie 头。"""
        cookies = [
            {"name": "SID", "value": "abc123", "domain": ".googlevideo.com", "secure": False},
            {"name": "HSID", "value": "xyz789", "domain": "rr1---sn-npoe7ndl.googlevideo.com", "secure": False},
            {"name": "unrelated", "value": "nope", "domain": ".otherdomain.com", "secure": False},
        ]
        header = AudioDownloader._build_cookie_header(cookies, TARGET_URL)
        assert header == "SID=abc123; HSID=xyz789"


# ── 2. 阶段级重试集成测试 ────────────────────────────────────────────

@pytest.fixture
def settings():
    """构造 AudioDownloader 所需的最小配置（mock）。"""
    s = MagicMock()
    s.cdp_use_curl_cffi = True
    s.cdp_enable_multipart = True
    s.cdp_multipart_min_size = 1
    s.data_dir = Path("/tmp/test_cookie_retry")
    s.cdp_convert_to_m4a = False
    return s


@pytest.fixture
def audio_downloader(settings):
    """创建 AudioDownloader 实例（mock 所有外部依赖）。"""
    return AudioDownloader(settings=settings, downloader_name="cdp")


@pytest.fixture
def audio_info():
    """构造触发分片下载的音频信息对象（10MB，超过 min_size）。"""
    return AudioInfo(
        url=TARGET_URL,
        title="test_video",
        ext="m4a",
        mime_type="audio/mp4",
        itag=140,
        filesize=10 * 1024 * 1024,
    )


# 匹配 TARGET_URL 域的 cookie（googlevideo.com 后缀命中）
MATCHING_COOKIES = [
    {"name": "SID", "value": "sid_value", "domain": ".googlevideo.com", "secure": False},
]

# 不匹配 TARGET_URL 域的 cookie（域名完全无关）
NON_MATCHING_COOKIES = [
    {"name": "unrelated", "value": "xxx", "domain": ".otherdomain.com", "secure": False},
]


@pytest.mark.asyncio
async def test_multipart_403_cookie_retry_succeeds(audio_downloader, audio_info, tmp_path):
    """
    场景：分片下载首次 403，带 cookie 重试一次后成功。
    期望：返回文件路径；重试请求 headers 携带合并后的 Cookie 头；仅调用 2 次分片下载。
    """
    call_headers = []

    async def mock_multipart(url, target_path, expected_size, headers):
        call_headers.append(dict(headers))
        if len(call_headers) == 1:
            raise _err_403("first attempt")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_multipart),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            cookies=MATCHING_COOKIES,
        )

    assert result is not None
    assert result.exists()
    assert len(call_headers) == 2
    # 第一次请求不带 Cookie
    assert "Cookie" not in call_headers[0]
    # 第二次（重试）请求带上合并后的 Cookie 头，且保留原有 headers
    assert call_headers[1]["Cookie"] == "SID=sid_value"
    assert call_headers[1]["user-agent"] == "test-ua"


@pytest.mark.asyncio
async def test_multipart_403_cookie_retry_still_403_falls_back_to_single_thread(
    audio_downloader, audio_info, tmp_path
):
    """
    场景：分片下载首次 403，cookie 重试仍 403，应降级到单线程下载。
    期望：分片下载总共只被调用 2 次（原始 1 次 + cookie 重试 1 次，不再叠加）；
          单线程下载被调用并成功。
    """
    multipart_calls = []
    single_thread_calls = []

    async def mock_multipart(url, target_path, expected_size, headers):
        multipart_calls.append(dict(headers))
        raise _err_403("multipart always 403")

    async def mock_single(url, target_path, expected_size, headers):
        single_thread_calls.append(dict(headers))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_single),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            cookies=MATCHING_COOKIES,
        )

    assert result is not None
    # 分片阶段：原始 1 次 + cookie 重试 1 次，不多不少
    assert len(multipart_calls) == 2
    assert "Cookie" not in multipart_calls[0]
    assert multipart_calls[1]["Cookie"] == "SID=sid_value"
    # 单线程阶段：正常降级触发，此处未携带 cookie（单线程首次尝试沿用原始 headers）
    assert len(single_thread_calls) == 1
    assert "Cookie" not in single_thread_calls[0]


@pytest.mark.asyncio
async def test_no_matching_cookie_skips_retry(audio_downloader, audio_info, tmp_path):
    """
    场景：cookie 列表非空，但没有一条命中目标 URL 域名。
    期望：不触发重试，分片下载只调用 1 次即降级到单线程。
    """
    multipart_calls = []
    single_thread_calls = []

    async def mock_multipart(url, target_path, expected_size, headers):
        multipart_calls.append(dict(headers))
        raise _err_403("no match")

    async def mock_single(url, target_path, expected_size, headers):
        single_thread_calls.append(dict(headers))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_single),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            cookies=NON_MATCHING_COOKIES,
        )

    assert len(multipart_calls) == 1
    assert len(single_thread_calls) == 1


@pytest.mark.asyncio
async def test_headers_already_have_cookie_skips_retry(audio_downloader, audio_info, tmp_path):
    """
    场景：原始 headers 本来就带 Cookie（has_cookie=True 场景，即浏览器
    捕获到的真实请求本身已经携带 Cookie 头）。
    期望：完全不触发新逻辑，分片下载只调用 1 次即正常降级。
    """
    multipart_calls = []
    single_thread_calls = []

    async def mock_multipart(url, target_path, expected_size, headers):
        multipart_calls.append(dict(headers))
        raise _err_403("already has cookie")

    async def mock_single(url, target_path, expected_size, headers):
        single_thread_calls.append(dict(headers))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_single),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua", "Cookie": "existing=already-there"},
            cookies=MATCHING_COOKIES,
        )

    assert len(multipart_calls) == 1
    assert len(single_thread_calls) == 1


@pytest.mark.asyncio
async def test_cookies_none_zero_behavior_change(audio_downloader, audio_info, tmp_path):
    """
    场景：cookies 参数为 None（未传入，向后兼容默认值）。
    期望：不触发重试逻辑，分片下载只调用 1 次，行为与改动前完全一致。
    """
    multipart_calls = []
    single_thread_calls = []

    async def mock_multipart(url, target_path, expected_size, headers):
        multipart_calls.append(dict(headers))
        raise _err_403("no cookies provided")

    async def mock_single(url, target_path, expected_size, headers):
        single_thread_calls.append(dict(headers))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(audio_downloader, "_download_with_curl_cffi_multipart", mock_multipart),
        patch.object(audio_downloader, "_download_with_curl_cffi", mock_single),
        patch.object(audio_downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        await audio_downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            # cookies 未传入，走默认值 None
        )

    assert len(multipart_calls) == 1
    assert len(single_thread_calls) == 1


@pytest.mark.asyncio
async def test_single_thread_403_cookie_retry_succeeds(settings, tmp_path):
    """
    场景：分片下载被禁用（走单线程直接下载），单线程首次 403，
    带 cookie 重试一次后成功。
    期望：单线程下载被调用 2 次；重试请求携带合并后的 Cookie 头。
    """
    settings.cdp_enable_multipart = False
    downloader = AudioDownloader(settings=settings, downloader_name="cdp")

    audio_info = AudioInfo(
        url=TARGET_URL,
        title="test_video",
        ext="m4a",
        mime_type="audio/mp4",
        itag=140,
        filesize=1 * 1024 * 1024,
    )

    call_headers = []

    async def mock_single(url, target_path, expected_size, headers):
        call_headers.append(dict(headers))
        if len(call_headers) == 1:
            raise _err_403("single-thread first attempt")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"fake audio data")
        return True

    with (
        patch.object(downloader, "_download_with_curl_cffi", mock_single),
        patch.object(downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            cookies=MATCHING_COOKIES,
        )

    assert result is not None
    assert len(call_headers) == 2
    assert "Cookie" not in call_headers[0]
    assert call_headers[1]["Cookie"] == "SID=sid_value"


@pytest.mark.asyncio
async def test_single_thread_403_cookie_retry_still_403_falls_back_to_cdn(settings, tmp_path):
    """
    场景：单线程下载首次 403，cookie 重试仍 403，应继续走 CDN 节点切换
    （而不是无限重试）。
    期望：单线程下载被调用 2 次（原始 + cookie 重试），之后不再有第三次
    针对原始 URL 的调用；最终走 yt-dlp 兜底也应被触发（因为此 URL 没有
    额外的 mn 备用节点，CDN 切换会立即跳过）。
    """
    settings.cdp_enable_multipart = False
    downloader = AudioDownloader(settings=settings, downloader_name="cdp")

    audio_info = AudioInfo(
        url=TARGET_URL,
        title="test_video",
        ext="m4a",
        mime_type="audio/mp4",
        itag=140,
        filesize=1 * 1024 * 1024,
    )

    single_thread_calls = []
    ytdlp_called = {"called": False}
    fake_file = tmp_path / "fake.m4a"

    async def mock_single(url, target_path, expected_size, headers):
        single_thread_calls.append(dict(headers))
        raise _err_403("single-thread always 403")

    async def mock_ytdlp(video_url, cookie_file, output_dir, expected_filename):
        ytdlp_called["called"] = True
        fake_file.write_bytes(b"ytdlp audio")
        return fake_file

    with (
        patch.object(downloader, "_download_with_curl_cffi", mock_single),
        patch.object(downloader, "_download_with_ytdlp", mock_ytdlp),
        patch.object(downloader, "_convert_to_m4a_if_needed", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        result = await downloader.download_audio(
            audio_info=audio_info,
            video_id="test123",
            task_id="task456",
            output_dir=tmp_path,
            headers={"user-agent": "test-ua"},
            cookies=MATCHING_COOKIES,
        )

    assert result == fake_file
    assert ytdlp_called["called"] is True
    # TARGET_URL 没有 mn 参数（无备用节点），因此 CDN 切换阶段不会再调用 _download_with_curl_cffi
    assert len(single_thread_calls) == 2
    assert "Cookie" not in single_thread_calls[0]
    assert single_thread_calls[1]["Cookie"] == "SID=sid_value"
