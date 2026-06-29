"""Tests for config.py — load/save round-trip."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from config import Config, DEFAULTS


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Path:
    return tmp_path / "test.ini"


def test_defaults_applied(tmp_cfg: Path) -> None:
    cfg = Config(tmp_cfg)
    cfg.load()
    assert cfg.ui_max_lines == int(DEFAULTS["ui"]["max_log_lines"])
    assert cfg.queue_max_size == int(DEFAULTS["advanced"]["queue_max_size"])
    assert cfg.auto_recovery is True
    assert cfg.start_minimized is False


def test_save_and_reload(tmp_cfg: Path) -> None:
    cfg = Config(tmp_cfg)
    cfg.load()
    cfg.log_folder = "/tmp/logs"
    cfg.auto_recovery = False
    cfg.ui_max_lines = 500
    cfg.save()

    cfg2 = Config(tmp_cfg)
    cfg2.load()
    assert cfg2.log_folder == "/tmp/logs"
    assert cfg2.auto_recovery is False
    assert cfg2.ui_max_lines == 500


def test_missing_file_uses_defaults(tmp_cfg: Path) -> None:
    cfg = Config(tmp_cfg)
    cfg.load()
    assert cfg.modifier_hold_threshold_sec == pytest.approx(3.0)
    assert cfg.rapid_toggle_count == 3
    assert cfg.rapid_toggle_window_sec == pytest.approx(2.0)


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "cfg.ini"
    cfg = Config(deep)
    cfg.load()
    cfg.log_folder = "/logs"
    cfg.save()
    assert deep.exists()


def test_preferred_layout_roundtrip(tmp_cfg: Path) -> None:
    cfg = Config(tmp_cfg)
    cfg.load()
    cfg.preferred_layout = "en-US"
    cfg.save()

    cfg2 = Config(tmp_cfg)
    cfg2.load()
    assert cfg2.preferred_layout == "en-US"


def test_advanced_values(tmp_cfg: Path) -> None:
    cfg = Config(tmp_cfg)
    cfg.load()
    assert cfg.backlog_warn_threshold == int(DEFAULTS["advanced"]["backlog_warn_threshold"])
    assert cfg.window_width == int(DEFAULTS["ui"]["window_width"])
    assert cfg.window_height == int(DEFAULTS["ui"]["window_height"])
