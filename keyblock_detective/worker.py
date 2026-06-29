"""
Worker thread for KeyBlock Detective.

Drains EVENT_QUEUE, detects suspicious patterns, enriches events with
system state, writes to the event logger, and pushes display dicts to ui_queue.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from typing import Callable

_log = logging.getLogger(__name__)

# VK codes relevant to pattern detection
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_MENU = 0x12      # Alt
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91

WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105

_SHIFT_VKS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
_CTRL_VKS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
_ALT_VKS = {VK_MENU, VK_LMENU, VK_RMENU}
_MOD_VKS = _SHIFT_VKS | _CTRL_VKS | _ALT_VKS | {VK_LWIN, VK_RWIN}
_LOCK_VKS = {VK_CAPITAL, VK_NUMLOCK, VK_SCROLL}


class WorkerThread(threading.Thread):
    """
    Drains ``event_queue``, enriches events, detects patterns,
    and forwards display dicts to ``ui_queue``.
    """

    def __init__(
        self,
        event_queue: queue.Queue,
        ui_queue: queue.Queue,
        logger,                     # event_logger.EventLogger instance
        state_reader: Callable[[], dict],  # state_manager.read_state
        config,                     # config.Config instance
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="WorkerThread", daemon=True)
        self._eq = event_queue
        self._uq = ui_queue
        self._logger = logger
        self._read_state = state_reader
        self._cfg = config
        self._stop = stop_event

        # ── Pattern-detection state ───────────────────────────────────
        self._consecutive_shift = 0     # SHIFT×5 counter
        self._last_non_shift_vk = None

        # modifier hold tracking: vk → time of last KEY_DOWN
        self._mod_down_times: dict[int, float] = {}

        # rapid lock-toggle: vk → deque of toggle timestamps
        self._lock_toggle_times: dict[int, collections.deque] = {
            vk: collections.deque() for vk in _LOCK_VKS
        }

        # layout change detection
        self._last_layout: str | None = None

        # accessibility state tracking
        self._last_sticky: int | None = None
        self._last_filter: int | None = None
        self._last_toggle_keys: int | None = None

    # ──────────────────────────────────────────────────────────────────
    # Thread main
    # ──────────────────────────────────────────────────────────────────

    def run(self) -> None:
        _log.info("WorkerThread started")
        while not self._stop.is_set():
            self._check_backlog()
            self._check_modifier_holds()
            try:
                ev = self._eq.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._process_event(ev)
            except Exception as exc:
                _log.error("WorkerThread error processing event: %s", exc)
            finally:
                self._eq.task_done()

        # Drain remaining items before exit
        _log.info("WorkerThread draining remaining events…")
        while True:
            try:
                ev = self._eq.get_nowait()
                self._process_event(ev)
                self._eq.task_done()
            except queue.Empty:
                break
            except Exception as exc:
                _log.error("WorkerThread drain error: %s", exc)

        try:
            self._logger.flush()
        except Exception:
            pass
        _log.info("WorkerThread exited")

    # ──────────────────────────────────────────────────────────────────
    # Event processing
    # ──────────────────────────────────────────────────────────────────

    def _process_event(self, ev: dict) -> None:
        ev_type = ev.get("type", "")

        if ev_type == "SESSION":
            self._handle_session_event(ev)
            return

        if ev_type == "ERROR":
            self._logger.write(ev)
            self._push_ui(ev)
            return

        # Raw keyboard event from hook
        self._handle_key_event(ev)

    def _handle_session_event(self, ev: dict) -> None:
        import os
        from datetime import datetime
        detail = ev.get("detail", "")
        enriched = {
            "timestamp": datetime.fromtimestamp(ev["ts"]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "type": "SESSION",
            "key": detail,
            "vk": "",
            "sc": "",
            "ext": 0,
            "inj": 0,
            "note": detail,
            **self._safe_read_state(),
        }
        self._logger.write(enriched)
        self._push_ui(enriched)

    def _handle_key_event(self, ev: dict) -> None:
        from datetime import datetime

        vk = ev.get("vk", 0)
        transition = ev.get("transition", WM_KEYDOWN)
        is_down = transition in (WM_KEYDOWN, WM_SYSKEYDOWN)
        ts = ev.get("ts", time.time())
        flags = ev.get("flags", 0)
        ext = 1 if (flags & 0x01) else 0
        inj = 1 if (flags & 0x10) else 0

        # Update modifier hold tracking
        if vk in _MOD_VKS:
            if is_down:
                self._mod_down_times.setdefault(vk, ts)
            else:
                self._mod_down_times.pop(vk, None)

        # Rapid lock-toggle tracking
        if vk in _LOCK_VKS and is_down:
            dq = self._lock_toggle_times[vk]
            dq.append(ts)
            window = self._cfg.rapid_toggle_window_sec
            while dq and ts - dq[0] > window:
                dq.popleft()

        state = self._safe_read_state()
        key_name = _vk_name(vk)

        enriched = {
            "timestamp": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "type": "KEY_DOWN" if is_down else "KEY_UP",
            "key": key_name,
            "vk": vk,
            "sc": ev.get("sc", 0),
            "ext": ext,
            "inj": inj,
            "note": "",
            **state,
        }

        # ── Pattern detection ─────────────────────────────────────────
        detected_patterns = self._detect_patterns(vk, is_down, ts, state)
        if detected_patterns:
            enriched["note"] = "; ".join(detected_patterns)

        self._logger.write(enriched)
        self._push_ui(enriched)

        # Auto-recovery hooks
        if self._cfg.auto_recovery:
            self._auto_recover(detected_patterns, state)

        # Layout / accessibility change detection
        self._detect_state_changes(state)

    # ──────────────────────────────────────────────────────────────────
    # Pattern detectors
    # ──────────────────────────────────────────────────────────────────

    def _detect_patterns(self, vk: int, is_down: bool, ts: float, state: dict) -> list[str]:
        patterns: list[str] = []

        if is_down:
            # SHIFT×5
            if vk in _SHIFT_VKS:
                self._consecutive_shift += 1
                if self._consecutive_shift >= 5:
                    patterns.append("KEY_COMBO SHIFT×5")
                    self._consecutive_shift = 0
            else:
                self._consecutive_shift = 0

            # Modifier combos
            mods = state.get("mods", "NONE")
            if vk in _ALT_VKS and ("LSHIFT" in mods or "RSHIFT" in mods):
                patterns.append("KEY_COMBO ALT+SHIFT")
            if vk in _SHIFT_VKS and ("LCTRL" in mods or "RCTRL" in mods):
                patterns.append("KEY_COMBO CTRL+SHIFT")
            if vk in _ALT_VKS and ("LCTRL" in mods or "RCTRL" in mods):
                patterns.append("KEY_COMBO CTRL+ALT")
            if vk in _SHIFT_VKS and ("LALT" in mods or "RALT" in mods):
                patterns.append("KEY_COMBO ALT+SHIFT")

            # Rapid lock toggle
            for lock_vk, label in [
                (VK_CAPITAL, "CAPS"), (VK_NUMLOCK, "NUMLOCK"), (VK_SCROLL, "SCROLL")
            ]:
                dq = self._lock_toggle_times.get(lock_vk)
                if dq and len(dq) >= self._cfg.rapid_toggle_count:
                    patterns.append(f"STATE_CHANGE RAPID_{label}_TOGGLE")

        return patterns

    def _check_modifier_holds(self) -> None:
        threshold = self._cfg.modifier_hold_threshold_sec
        now = time.time()
        for vk, down_ts in list(self._mod_down_times.items()):
            held = now - down_ts
            if held > threshold:
                note = f"STATE_CHANGE MODIFIER_HOLD {_vk_name(vk)} {held:.1f}s"
                ev = {
                    "timestamp": _ts_now(),
                    "type": "STATE_CHANGE",
                    "key": _vk_name(vk),
                    "vk": vk,
                    "sc": "",
                    "ext": 0,
                    "inj": 0,
                    "note": note,
                    **self._safe_read_state(),
                }
                self._logger.write(ev)
                self._push_ui(ev)
                # Remove so we don't spam; re-arms on next KEY_DOWN
                del self._mod_down_times[vk]

    def _detect_state_changes(self, state: dict) -> None:
        layout = state.get("layout", "N/A")
        if self._last_layout is not None and layout != self._last_layout:
            note = f"STATE_CHANGE LAYOUT_CHANGE {self._last_layout}→{layout}"
            self._emit_state_change(note, state)
        self._last_layout = layout

        sticky = state.get("sticky", 0)
        if self._last_sticky is not None and sticky != self._last_sticky:
            self._emit_state_change(f"STATE_CHANGE STICKY_KEYS {'ON' if sticky else 'OFF'}", state)
        self._last_sticky = sticky

        filt = state.get("filter", 0)
        if self._last_filter is not None and filt != self._last_filter:
            self._emit_state_change(f"STATE_CHANGE FILTER_KEYS {'ON' if filt else 'OFF'}", state)
        self._last_filter = filt

        tg = state.get("toggle", 0)
        if self._last_toggle_keys is not None and tg != self._last_toggle_keys:
            self._emit_state_change(f"STATE_CHANGE TOGGLE_KEYS {'ON' if tg else 'OFF'}", state)
        self._last_toggle_keys = tg

    def _emit_state_change(self, note: str, state: dict) -> None:
        ev = {
            "timestamp": _ts_now(),
            "type": "STATE_CHANGE",
            "key": "",
            "vk": "",
            "sc": "",
            "ext": 0,
            "inj": 0,
            "note": note,
            **state,
        }
        self._logger.write(ev)
        self._push_ui(ev)

    # ──────────────────────────────────────────────────────────────────
    # Auto-recovery
    # ──────────────────────────────────────────────────────────────────

    def _auto_recover(self, patterns: list[str], state: dict) -> None:
        if not patterns:
            return
        try:
            import state_manager
        except ImportError:
            return

        for pattern in patterns:
            if "SHIFT×5" in pattern:
                result = state_manager.disable_sticky_keys()
                self._log_auto_fix("disable_sticky_keys", "SHIFT×5", result, state)
            if "MODIFIER_HOLD" in pattern:
                result = state_manager.release_stuck_modifiers()
                self._log_auto_fix("release_stuck_modifiers", "MODIFIER_HOLD", result, state)

        # Accessibility state flipped ON → disable
        if state.get("sticky"):
            result = state_manager.disable_sticky_keys()
            self._log_auto_fix("disable_sticky_keys", "STICKY_ON", result, state)
        if state.get("filter"):
            result = state_manager.disable_filter_keys()
            self._log_auto_fix("disable_filter_keys", "FILTER_ON", result, state)
        if state.get("toggle"):
            result = state_manager.disable_toggle_keys()
            self._log_auto_fix("disable_toggle_keys", "TOGGLE_ON", result, state)

    def _log_auto_fix(self, action: str, trigger: str, result: str, state: dict) -> None:
        ev = {
            "timestamp": _ts_now(),
            "type": "AUTO_FIX",
            "key": action,
            "vk": "",
            "sc": "",
            "ext": 0,
            "inj": 0,
            "note": f"Trigger:{trigger} Result:{result}",
            **state,
        }
        self._logger.write(ev)
        self._push_ui(ev)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _check_backlog(self) -> None:
        threshold = self._cfg.backlog_warn_threshold
        size = self._eq.qsize()
        if size > threshold:
            self._logger.write_error("Queue backlog exceeded threshold", size)

    def _safe_read_state(self) -> dict:
        try:
            return self._read_state()
        except Exception as exc:
            _log.debug("read_state error: %s", exc)
            return {
                "caps": "?", "num": "?", "scroll": "?",
                "sticky": "?", "filter": "?", "toggle": "?",
                "layout": "N/A", "mods": "NONE",
                "user": "N/A", "app": "N/A", "window": "N/A",
            }

    def _push_ui(self, ev: dict) -> None:
        try:
            self._uq.put_nowait(ev)
        except queue.Full:
            pass


# ──────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────

_VK_NAMES: dict[int, str] = {
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "RETURN", 0x10: "SHIFT",
    0x11: "CTRL", 0x12: "ALT", 0x13: "PAUSE", 0x14: "CAPS",
    0x1B: "ESCAPE", 0x20: "SPACE", 0x21: "PGUP", 0x22: "PGDN",
    0x23: "END", 0x24: "HOME", 0x25: "LEFT", 0x26: "UP",
    0x27: "RIGHT", 0x28: "DOWN", 0x2C: "PRTSCR", 0x2D: "INSERT",
    0x2E: "DELETE", 0x5B: "LWIN", 0x5C: "RWIN", 0x90: "NUMLOCK",
    0x91: "SCROLL", 0xA0: "LSHIFT", 0xA1: "RSHIFT",
    0xA2: "LCTRL", 0xA3: "RCTRL", 0xA4: "LALT", 0xA5: "RALT",
}


def _vk_name(vk: int) -> str:
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return f"VK_{vk:#04x}"


def _ts_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
