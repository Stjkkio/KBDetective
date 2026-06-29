"""Tests for event_logger.py — format and write behaviour."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from event_logger import EventLogger, _format_line, _now


# ------------------------------------------------------------------
# _format_line unit tests
# ------------------------------------------------------------------

def _sample_ev(**overrides) -> dict:
    base = {
        "timestamp": "2024-01-15 12:00:00.000",
        "type": "KEY_DOWN",
        "key": "VK_SHIFT",
        "vk": 0x10,
        "sc": 42,
        "ext": 0,
        "inj": 0,
        "app": "notepad.exe",
        "window": "Untitled - Notepad",
        "user": "TestUser",
        "layout": "en-US",
        "caps": 0,
        "num": 1,
        "scroll": 0,
        "sticky": 0,
        "filter": 0,
        "toggle": 0,
        "mods": "NONE",
        "note": "",
    }
    base.update(overrides)
    return base


def test_format_line_pipe_count() -> None:
    line = _format_line(_sample_ev())
    # 11 fields → 10 separators
    assert line.count(" | ") == 10


def test_format_line_timestamp_present() -> None:
    ev = _sample_ev(timestamp="2024-01-15 12:00:00.000")
    line = _format_line(ev)
    assert line.startswith("2024-01-15 12:00:00.000")


def test_format_line_vk_sc_present() -> None:
    ev = _sample_ev(vk=0x10, sc=42)
    line = _format_line(ev)
    assert "VK: 16 SC: 42" in line or "VK:16" in line or "VK: 16" in line


def test_format_line_app_window() -> None:
    ev = _sample_ev(app="notepad.exe", window="My Window")
    line = _format_line(ev)
    assert 'App:notepad.exe' in line
    assert 'Window:"My Window"' in line


def test_format_line_state_fields() -> None:
    ev = _sample_ev(caps=1, num=0, scroll=1, sticky=1, filter=0, toggle=0, mods="LSHIFT")
    line = _format_line(ev)
    assert "Caps:1" in line
    assert "Num:0" in line
    assert "Scroll:1" in line
    assert "Sticky:1" in line
    assert "Mods:LSHIFT" in line


def test_format_line_note() -> None:
    ev = _sample_ev(note="SHIFT×5 detected")
    line = _format_line(ev)
    assert line.endswith("SHIFT×5 detected")


def test_format_line_flags() -> None:
    ev = _sample_ev(ext=1, inj=0)
    line = _format_line(ev)
    assert "FLAGS:EXT=1 INJ=0" in line


# ------------------------------------------------------------------
# EventLogger integration tests
# ------------------------------------------------------------------

def test_logger_creates_file(tmp_path: Path) -> None:
    logger = EventLogger(str(tmp_path))
    ev = _sample_ev()
    logger.write(ev)
    logger.close()
    log_files = list(tmp_path.glob("keyblock_*.log"))
    assert len(log_files) == 1


def test_logger_writes_formatted_line(tmp_path: Path) -> None:
    logger = EventLogger(str(tmp_path))
    ev = _sample_ev(note="test note")
    logger.write(ev)
    logger.close()
    content = list(tmp_path.glob("keyblock_*.log"))[0].read_text(encoding="utf-8")
    assert "test note" in content
    assert content.count(" | ") >= 10


def test_logger_write_error(tmp_path: Path) -> None:
    logger = EventLogger(str(tmp_path))
    logger.write_error("Test error", "detail info")
    logger.close()
    content = list(tmp_path.glob("keyblock_*.log"))[0].read_text(encoding="utf-8")
    assert "ERROR" in content
    assert "Test error" in content


def test_logger_flush_on_session_event(tmp_path: Path) -> None:
    """SESSION events trigger immediate flush — file should have content before close."""
    logger = EventLogger(str(tmp_path))
    ev = _sample_ev(type="SESSION", note="LOCK")
    logger.write(ev)
    # Read without calling close() — flush must have happened
    content = list(tmp_path.glob("keyblock_*.log"))[0].read_text(encoding="utf-8")
    assert "SESSION" in content
    logger.close()


def test_logger_notifies_ui_error_queue(tmp_path: Path) -> None:
    import queue as q
    ui_q = q.Queue()
    # Point at a non-writable path to trigger OSError
    logger = EventLogger("/nonexistent_path_xyz/logs", ui_error_queue=ui_q)
    item = ui_q.get(timeout=1)
    assert item["type"] == "UI_ERROR"


def test_now_format() -> None:
    ts = _now()
    # e.g. "2024-01-15 12:00:00.123"
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", ts)
