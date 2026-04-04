"""
Tests for file_service.py resource management fixes.

Covers:
- Fix 1: check_disk_space() uses statvfs directly (O(1), no directory scan)
- Fix 2: _get_dir_size() uses os.scandir instead of rglob (fd-safe)
- Fix 10: _cleanup_empty_dirs() uses next(iterdir()) instead of any(iterdir())
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.file_service import FileService


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory structure."""
    audio_dir = tmp_path / "files" / "audio"
    transcript_dir = tmp_path / "files" / "transcript"
    audio_dir.mkdir(parents=True)
    transcript_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def file_service(temp_data_dir):
    """Create a FileService instance with mocked dependencies."""
    settings = MagicMock()
    settings.audio_dir = temp_data_dir / "files" / "audio"
    settings.transcript_dir = temp_data_dir / "files" / "transcript"
    settings.file_retention_hours = 24
    db = AsyncMock()
    service = FileService(db, settings)
    service.data_dir = temp_data_dir
    return service


class TestCheckDiskSpace:
    """Fix 1: check_disk_space should use statvfs directly, not scan directories."""

    def test_check_disk_space_returns_true_when_enough(self, file_service):
        """Sufficient disk space returns True."""
        result = file_service.check_disk_space(required_mb=1)
        assert result is True

    def test_check_disk_space_does_not_call_get_disk_usage(self, file_service):
        """check_disk_space must NOT call get_disk_usage (which scans dirs)."""
        with patch.object(file_service, "get_disk_usage") as mock:
            file_service.check_disk_space(required_mb=1)
            mock.assert_not_called()

    def test_check_disk_space_uses_get_free_space_bytes(self, file_service):
        """check_disk_space should use the lightweight _get_free_space_bytes."""
        with patch.object(file_service, "_get_free_space_bytes", return_value=500 * 1024 * 1024):
            assert file_service.check_disk_space(required_mb=100) is True
            assert file_service.check_disk_space(required_mb=1000) is False

    def test_check_disk_space_returns_false_when_insufficient(self, file_service):
        """Insufficient disk space returns False."""
        with patch.object(file_service, "_get_free_space_bytes", return_value=10 * 1024 * 1024):
            result = file_service.check_disk_space(required_mb=100)
            assert result is False

    def test_get_free_space_bytes_returns_positive(self, file_service):
        """_get_free_space_bytes should return a positive value on a normal filesystem."""
        result = file_service._get_free_space_bytes()
        assert result > 0


class TestGetDirSize:
    """Fix 2: _get_dir_size should use os.scandir, not rglob."""

    def test_empty_dir(self, file_service):
        """Empty directory returns 0."""
        result = file_service._get_dir_size(file_service.settings.audio_dir)
        assert result == 0

    def test_nonexistent_dir(self, file_service, tmp_path):
        """Nonexistent directory returns 0."""
        result = file_service._get_dir_size(tmp_path / "nonexistent")
        assert result == 0

    def test_single_file(self, file_service):
        """Directory with one file returns its size."""
        test_file = file_service.settings.audio_dir / "test.mp3"
        test_file.write_bytes(b"x" * 1024)
        result = file_service._get_dir_size(file_service.settings.audio_dir)
        assert result == 1024

    def test_nested_files(self, file_service):
        """Recursively counts files in subdirectories."""
        audio_dir = file_service.settings.audio_dir
        (audio_dir / "sub").mkdir()
        (audio_dir / "file1.mp3").write_bytes(b"a" * 100)
        (audio_dir / "sub" / "file2.mp3").write_bytes(b"b" * 200)
        result = file_service._get_dir_size(audio_dir)
        assert result == 300

    def test_does_not_follow_symlinks(self, file_service, tmp_path):
        """Should not follow symlinks to avoid loops and external paths."""
        audio_dir = file_service.settings.audio_dir
        real_file = tmp_path / "external_file.txt"
        real_file.write_bytes(b"x" * 500)
        link = audio_dir / "symlink.txt"
        try:
            link.symlink_to(real_file)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")
        result = file_service._get_dir_size(audio_dir)
        # symlink should not be followed, so size should be 0 (or symlink size, not target size)
        assert result == 0

    def test_permission_error_skipped(self, file_service):
        """Directories without permission are skipped gracefully."""
        audio_dir = file_service.settings.audio_dir
        restricted = audio_dir / "restricted"
        restricted.mkdir()
        (restricted / "secret.mp3").write_bytes(b"s" * 100)
        # Write a file outside restricted for baseline
        (audio_dir / "normal.mp3").write_bytes(b"n" * 50)

        original_scandir = os.scandir

        def mock_scandir(path):
            if str(path) == str(restricted):
                raise PermissionError("Access denied")
            return original_scandir(path)

        with patch("os.scandir", side_effect=mock_scandir):
            result = file_service._get_dir_size(audio_dir)
            # Should include normal.mp3 but not restricted/secret.mp3
            assert result == 50

    def test_many_files_no_rglob(self, file_service):
        """Verify rglob is not used (no pathlib.Path.rglob call)."""
        audio_dir = file_service.settings.audio_dir
        # Create some files
        for i in range(10):
            (audio_dir / f"file_{i}.mp3").write_bytes(b"x" * 100)

        with patch("pathlib.Path.rglob", side_effect=AssertionError("rglob should not be called")):
            result = file_service._get_dir_size(audio_dir)
            assert result == 1000


class TestCleanupEmptyDirs:
    """Fix 10: _cleanup_empty_dirs should use next(iterdir()) instead of any(iterdir())."""

    def test_removes_empty_subdirs(self, file_service):
        """Empty subdirectories are removed."""
        empty_dir = file_service.settings.audio_dir / "empty_subdir"
        empty_dir.mkdir()
        file_service._cleanup_empty_dirs()
        assert not empty_dir.exists()

    def test_keeps_nonempty_subdirs(self, file_service):
        """Subdirectories with files are kept."""
        subdir = file_service.settings.audio_dir / "has_files"
        subdir.mkdir()
        (subdir / "file.mp3").write_bytes(b"data")
        file_service._cleanup_empty_dirs()
        assert subdir.exists()

    def test_no_error_on_empty_root(self, file_service):
        """No error when root dirs are empty."""
        file_service._cleanup_empty_dirs()  # Should not raise


class TestGetDiskUsage:
    """Verify get_disk_usage uses _get_free_space_bytes."""

    def test_returns_expected_keys(self, file_service):
        """get_disk_usage returns all expected keys."""
        usage = file_service.get_disk_usage()
        assert "audio_size" in usage
        assert "transcript_size" in usage
        assert "total_size" in usage
        assert "free_space" in usage
        assert usage["total_size"] == usage["audio_size"] + usage["transcript_size"]
        assert usage["free_space"] > 0
