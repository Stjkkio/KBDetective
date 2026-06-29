"""Tests for worker.py — pattern detection."""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Patch win32 imports before importing worker
import sys
for _mod in ["win32gui", "win32process", "win32api", "win32con", "win32ts"]:
    sys.modules.setdefault(_mod, MagicMock())

from worker import WorkerThread, WM_KEYDOWN, WM_KEYUP, VK_SHIFT, VK_LSHIFT, VK_RSHIFT, VK_CAPITAL, _vk_name


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_worker(auto_recovery: bool = False) -> tuple[WorkerThread, queue.Queue, list[dict]]:
    """Return (worker, event_queue, written_events)."""
    eq = queue.Queue()
    uq = queue.Queue()
    written: list[dict] = []

    logger = MagicMock()
    logger.write.side_effect = lambda ev: written.append(ev)
    logger.write_error.side_effect = lambda msg, detail="": None
    logger.flush.return_value = None

    cfg = MagicMock()
    cfg.auto_recovery = auto_recovery
    cfg.modifier_hold_threshold_sec = 3.0
    cfg.rapid_toggle_count = 3
    cfg.rapid_toggle_window_sec = 2.0
    cfg.backlog_warn_threshold = 500

    state_reader = MagicMock(return_value={
        "caps": 0, "num": 0, "scroll": 0,
        "sticky": 0, "filter": 0, "toggle": 0,
        "layout": "00000409",
        "mods": "NONE",
        "user": "TestUser",
        "app": "pytest",
        "window": "Test Window",
    })

    stop = threading.Event()
    w = WorkerThread(eq, uq, logger, state_reader, cfg, stop)
    return w, eq, written


def _key_ev(vk: int, transition: int = WM_KEYDOWN, ts: float | None = None) -> dict:
    return {
        "vk": vk,
        "sc": 42,
        "flags": 0,
        "time_ms": 0,
        "transition": transition,
        "ts": ts or time.time(),
    }


def _run_events(events: list[dict], auto_recovery: bool = False) -> list[dict]:
    """Feed events to a worker and collect all written log entries."""
    w, eq, written = _make_worker(auto_recovery=auto_recovery)

    for ev in events:
        eq.put(ev)

    stop = threading.Event()
    w._stop = stop

    # Drain without starting a thread
    for ev in events:
        try:
            item = eq.get_nowait()
            w._process_event(item)
            eq.task_done()
        except queue.Empty:
            pass

    return written


# ------------------------------------------------------------------
# SHIFT×5 detection
# ------------------------------------------------------------------

def test_shift5_detected() -> None:
    events = [_key_ev(VK_SHIFT) for _ in range(5)]
    written = _run_events(events)
    notes = [ev.get("note", "") for ev in written]
    assert any("SHIFT×5" in n for n in notes), f"Expected SHIFT×5 in notes: {notes}"


def test_shift5_resets_on_other_key() -> None:
    # 4 shifts, one other key, then 5 shifts — only the last 5 should fire
    events = [
        _key_ev(VK_SHIFT), _key_ev(VK_SHIFT), _key_ev(VK_SHIFT), _key_ev(VK_SHIFT),
        _key_ev(0x41),  # 'A'
        _key_ev(VK_SHIFT), _key_ev(VK_SHIFT), _key_ev(VK_SHIFT),
        _key_ev(VK_SHIFT), _key_ev(VK_SHIFT),
    ]
    written = _run_events(events)
    notes = [ev.get("note", "") for ev in written]
    # Exactly one SHIFT×5 detected (the second group)
    count = sum(1 for n in notes if "SHIFT×5" in n)
    assert count == 1, f"Expected exactly 1 SHIFT×5, got {count}"


def test_lshift_counts_for_shift5() -> None:
    events = [_key_ev(VK_LSHIFT) for _ in range(5)]
    written = _run_events(events)
    notes = [ev.get("note", "") for ev in written]
    assert any("SHIFT×5" in n for n in notes)


def test_rshift_counts_for_shift5() -> None:
    events = [_key_ev(VK_RSHIFT) for _ in range(5)]
    written = _run_events(events)
    notes = [ev.get("note", "") for ev in written]
    assert any("SHIFT×5" in n for n in notes)


# ------------------------------------------------------------------
# Modifier hold detection (mocked time)
# ------------------------------------------------------------------

def test_modifier_hold_detection() -> None:
    w, eq, written = _make_worker()

    now = time.time()
    # Simulate LSHIFT held 4 seconds ago
    w._mod_down_times[VK_LSHIFT] = now - 4.0
    w._check_modifier_holds()

    notes = [ev.get("note", "") for ev in written]
    assert any("MODIFIER_HOLD" in n for n in notes), f"notes: {notes}"


def test_modifier_not_held_below_threshold() -> None:
    w, eq, written = _make_worker()

    now = time.time()
    # Only 1 second — below default 3s threshold
    w._mod_down_times[VK_LSHIFT] = now - 1.0
    w._check_modifier_holds()

    assert len(written) == 0


def test_modifier_hold_clears_after_detection() -> None:
    w, eq, written = _make_worker()
    now = time.time()
    w._mod_down_times[VK_LSHIFT] = now - 5.0
    w._check_modifier_holds()
    # Should be removed to avoid spamming
    assert VK_LSHIFT not in w._mod_down_times


# ------------------------------------------------------------------
# Rapid lock toggle
# ------------------------------------------------------------------

def test_rapid_capslock_toggle() -> None:
    now = time.time()
    events = [_key_ev(VK_CAPITAL, ts=now + i * 0.3) for i in range(3)]
    written = _run_events(events)
    notes = [ev.get("note", "") for ev in written]
    assert any("RAPID_CAPS_TOGGLE" in n for n in notes), f"notes: {notes}"


# ------------------------------------------------------------------
# SESSION event pass-through
# ------------------------------------------------------------------

def test_session_lock_written() -> None:
    w, eq, written = _make_worker()
    w._process_event({"type": "SESSION", "detail": "LOCK", "ts": time.time()})
    types = [ev.get("type") for ev in written]
    assert "SESSION" in types


def test_session_unlock_written() -> None:
    w, eq, written = _make_worker()
    w._process_event({"type": "SESSION", "detail": "UNLOCK", "ts": time.time()})
    types = [ev.get("type") for ev in written]
    assert "SESSION" in types


# ------------------------------------------------------------------
# _vk_name utility
# ------------------------------------------------------------------

def test_vk_name_known_keys() -> None:
    assert _vk_name(0x10) == "SHIFT"
    assert _vk_name(0x14) == "CAPS"
    assert _vk_name(0xA0) == "LSHIFT"


def test_vk_name_alpha() -> None:
    assert _vk_name(0x41) == "A"
    assert _vk_name(0x5A) == "Z"


def test_vk_name_digits() -> None:
    assert _vk_name(0x30) == "0"
    assert _vk_name(0x39) == "9"


def test_vk_name_function_keys() -> None:
    assert _vk_name(0x70) == "F1"
    assert _vk_name(0x7B) == "F12"


def test_vk_name_unknown() -> None:
    name = _vk_name(0xFF)
    assert name.startswith("VK_")
