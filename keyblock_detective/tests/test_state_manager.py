"""Tests for state_manager.py — recovery actions with mocked Win32 calls."""

from __future__ import annotations

import ctypes
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure ctypes.windll exists (mock it on non-Windows) ──────────────────
if not hasattr(ctypes, "windll"):
    _windll = MagicMock()
    ctypes.windll = _windll

# Stub pywin32 modules so state_manager can be imported on any platform
for _mod in ["win32gui", "win32process", "win32api", "win32con", "win32ts"]:
    sys.modules.setdefault(_mod, MagicMock())

import state_manager
from state_manager import (
    SPI_SETSTICKYKEYS,
    SPI_SETFILTERKEYS,
    SPI_SETTOGGLEKEYS,
    disable_sticky_keys,
    disable_filter_keys,
    disable_toggle_keys,
    release_stuck_modifiers,
    full_recovery,
    _get_toggle_state,
)

# ── Helper: patch state_manager's ctypes.windll.user32 ────────────────────
# We patch via the module's reference so it works cross-platform.
_SM_SPI = "state_manager.ctypes.windll.user32.SystemParametersInfoW"
_SM_SI  = "state_manager.ctypes.windll.user32.SendInput"
_SM_GKS = "state_manager.ctypes.windll.user32.GetKeyState"


# ------------------------------------------------------------------
# disable_sticky_keys
# ------------------------------------------------------------------

def test_disable_sticky_keys_success() -> None:
    with patch(_SM_SPI, return_value=1) as mock_spi:
        result = disable_sticky_keys()
    assert result == "SUCCESS"
    mock_spi.assert_called_once()
    assert mock_spi.call_args[0][0] == SPI_SETSTICKYKEYS


def test_disable_sticky_keys_failure() -> None:
    with patch(_SM_SPI, return_value=0):
        result = disable_sticky_keys()
    assert result == "FAILED"


# ------------------------------------------------------------------
# disable_filter_keys
# ------------------------------------------------------------------

def test_disable_filter_keys_success() -> None:
    with patch(_SM_SPI, return_value=1) as mock_spi:
        result = disable_filter_keys()
    assert result == "SUCCESS"
    assert mock_spi.call_args[0][0] == SPI_SETFILTERKEYS


def test_disable_filter_keys_failure() -> None:
    with patch(_SM_SPI, return_value=0):
        result = disable_filter_keys()
    assert result == "FAILED"


# ------------------------------------------------------------------
# disable_toggle_keys
# ------------------------------------------------------------------

def test_disable_toggle_keys_success() -> None:
    with patch(_SM_SPI, return_value=1) as mock_spi:
        result = disable_toggle_keys()
    assert result == "SUCCESS"
    assert mock_spi.call_args[0][0] == SPI_SETTOGGLEKEYS


def test_disable_toggle_keys_failure() -> None:
    with patch(_SM_SPI, return_value=0):
        result = disable_toggle_keys()
    assert result == "FAILED"


# ------------------------------------------------------------------
# release_stuck_modifiers
# ------------------------------------------------------------------

def test_release_stuck_modifiers_calls_send_input() -> None:
    with patch(_SM_SI, return_value=1) as mock_si:
        result = release_stuck_modifiers()
    assert result == "SUCCESS"
    # 8 modifier VKs → 8 SendInput calls
    assert mock_si.call_count == 8


def test_release_stuck_modifiers_failure_on_exception() -> None:
    with patch(_SM_SI, side_effect=OSError("mock")):
        result = release_stuck_modifiers()
    assert result == "FAILED"


# ------------------------------------------------------------------
# full_recovery
# ------------------------------------------------------------------

def test_full_recovery_returns_four_steps() -> None:
    with patch("state_manager.disable_sticky_keys", return_value="SUCCESS"), \
         patch("state_manager.disable_filter_keys", return_value="SUCCESS"), \
         patch("state_manager.disable_toggle_keys", return_value="SUCCESS"), \
         patch("state_manager.release_stuck_modifiers", return_value="SUCCESS"):
        results = full_recovery()
    assert len(results) == 4
    names = [r[0] for r in results]
    assert "disable_sticky_keys" in names
    assert "release_stuck_modifiers" in names


def test_full_recovery_propagates_failures() -> None:
    with patch("state_manager.disable_sticky_keys", return_value="FAILED"), \
         patch("state_manager.disable_filter_keys", return_value="SUCCESS"), \
         patch("state_manager.disable_toggle_keys", return_value="SUCCESS"), \
         patch("state_manager.release_stuck_modifiers", return_value="FAILED"):
        results = full_recovery()
    result_map = dict(results)
    assert result_map["disable_sticky_keys"] == "FAILED"
    assert result_map["disable_filter_keys"] == "SUCCESS"
    assert result_map["release_stuck_modifiers"] == "FAILED"


def test_full_recovery_catches_exceptions() -> None:
    with patch("state_manager.disable_sticky_keys", side_effect=RuntimeError("boom")), \
         patch("state_manager.disable_filter_keys", return_value="SUCCESS"), \
         patch("state_manager.disable_toggle_keys", return_value="SUCCESS"), \
         patch("state_manager.release_stuck_modifiers", return_value="SUCCESS"):
        results = full_recovery()
    result_map = dict(results)
    assert "FAILED" in result_map["disable_sticky_keys"]


# ------------------------------------------------------------------
# _get_toggle_state
# ------------------------------------------------------------------

def test_get_toggle_state_on() -> None:
    with patch(_SM_GKS, return_value=0x0001):
        assert _get_toggle_state(0x14) == 1


def test_get_toggle_state_off() -> None:
    with patch(_SM_GKS, return_value=0x0000):
        assert _get_toggle_state(0x14) == 0


def test_get_toggle_state_exception_returns_zero() -> None:
    with patch(_SM_GKS, side_effect=OSError("mock")):
        assert _get_toggle_state(0x14) == 0
