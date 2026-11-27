from datetime import datetime, timezone

import pytest

from src.db.models import FileRecord, FileType
from src.services.files_view import build_files_response


def _file(file_id: str, file_type: FileType, **overrides) -> FileRecord:
    values = {
        "id": file_id,
        "video_id": "video-1",
        "file_type": file_type,
        "filename": f"{file_id}.srt",
        "filepath": f"files/{file_id}",
        "size": 42,
        "format": "srt",
        "quality": None,
        "language": "en" if file_type == FileType.TRANSCRIPT else None,
        "expires_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return FileRecord(**values)


def _urls(file: FileRecord) -> str:
    return f"/api/v1/files/{file.id}.{file.format or 'srt'}"


@pytest.mark.parametrize(
    ("audio", "transcript", "expected"),
    [
        (None, None, None),
        (_file("audio", FileType.AUDIO, format="webm", quality="128"), None, "audio"),
        (None, _file("transcript", FileType.TRANSCRIPT), "transcript"),
        (
            _file("audio", FileType.AUDIO, format="m4a", quality="192"),
            _file("transcript", FileType.TRANSCRIPT, format="vtt"),
            "both",
        ),
    ],
)
def test_build_files_response_projects_all_file_combinations(audio, transcript, expected):
    result = build_files_response(
        audio, transcript, audio_url=_urls, transcript_url=_urls
    )

    if expected is None:
        assert result is None
        return
    assert result is not None
    assert (result.audio is not None) == (expected in {"audio", "both"})
    assert (result.transcript is not None) == (expected in {"transcript", "both"})
    if result.audio:
        assert result.audio.format == audio.format
        assert result.audio.bitrate == int(audio.quality)
    if result.transcript:
        assert result.transcript.format == transcript.format
        assert result.transcript.language == transcript.language


def test_non_numeric_audio_quality_projects_no_bitrate():
    for quality in ("128k", "best", "²"):
        audio = _file("audio", FileType.AUDIO, quality=quality)
        result = build_files_response(
            audio, None, audio_url=_urls, transcript_url=_urls
        )

        assert result is not None
        assert result.audio is not None
        assert result.audio.bitrate is None


def test_transcript_format_uses_default_only_when_record_has_no_format():
    transcript = _file("transcript", FileType.TRANSCRIPT, format=None)

    result = build_files_response(
        None,
        transcript,
        audio_url=_urls,
        transcript_url=_urls,
        default_transcript_format="srt",
    )

    assert result is not None
    assert result.transcript is not None
    assert result.transcript.format == "srt"
    assert result.transcript.url.endswith(".srt")


def test_transcript_format_default_is_json_for_webhook_projection():
    transcript = _file("transcript", FileType.TRANSCRIPT, format=None)
    result = build_files_response(
        None, transcript, audio_url=_urls, transcript_url=_urls
    )

    assert result is not None
    assert result.transcript is not None
    assert result.transcript.format == "json"
