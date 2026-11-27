from typing import Callable, Optional

from src.api.schemas import FileInfoResponse, FilesResponse
from src.db.models import FileRecord


FileURL = Callable[[FileRecord], str]


def build_files_response(
    audio_file: Optional[FileRecord],
    transcript_file: Optional[FileRecord],
    *,
    audio_url: FileURL,
    transcript_url: FileURL,
) -> Optional[FilesResponse]:
    """Project file records into the shared API response shape."""
    if audio_file is None and transcript_file is None:
        return None

    audio = None
    if audio_file:
        audio = FileInfoResponse(
            url=audio_url(audio_file),
            size=audio_file.size,
            format=audio_file.format,
            bitrate=int(audio_file.quality) if audio_file.quality else None,
        )

    transcript = None
    if transcript_file:
        transcript = FileInfoResponse(
            url=transcript_url(transcript_file),
            size=transcript_file.size,
            format=transcript_file.format or "json",
            language=transcript_file.language,
        )

    return FilesResponse(audio=audio, transcript=transcript)
