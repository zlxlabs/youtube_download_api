"""
Tests for logger setup.

Regression test: setup_logger(json_logs=True) previously passed format=None
to loguru, which raises at runtime (loguru requires str or callable).
"""

import json

from src.utils.logger import logger, setup_logger


def _restore_default_logger() -> None:
    """恢复默认控制台 logger，避免影响其他测试。"""
    setup_logger(log_dir=None, debug=False)


def test_setup_logger_json_mode_writes_valid_json(tmp_path):
    """json_logs=True 时应正常初始化并写出可解析的 JSON 日志。"""
    try:
        setup_logger(log_dir=tmp_path, debug=False, json_logs=True)
        logger.info("json mode smoke test")
        logger.complete()

        log_files = list(tmp_path.glob("app_*.log"))
        assert log_files, "App log file not created"

        lines = [
            line
            for line in log_files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines, "Log file is empty"
        record = json.loads(lines[-1])
        assert record["record"]["message"] == "json mode smoke test"
    finally:
        _restore_default_logger()


def test_setup_logger_text_mode_writes_plain_text(tmp_path):
    """默认文本模式应写出包含消息的非 JSON 日志。"""
    try:
        setup_logger(log_dir=tmp_path, debug=False, json_logs=False)
        logger.info("text mode smoke test")
        logger.complete()

        log_files = list(tmp_path.glob("app_*.log"))
        assert log_files, "App log file not created"
        content = log_files[0].read_text(encoding="utf-8")
        assert "text mode smoke test" in content
    finally:
        _restore_default_logger()
