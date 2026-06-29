"""Structured pipe-delimited event logger for KeyBlock Detective."""

from __future__ import annotations

import logging
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

# Events of these types force an immediate flush to disk.
_FLUSH_TYPES = {"SESSION", "ERROR", "AUTO_FIX"}


class EventLogger:
    """
    Thread-safe, buffered writer.

    write()       — format and buffer an event dict
    write_error() — shorthand for error events
    flush()       — force-write buffer to disk
    close()       — flush + close the file handle
    """

    def __init__(
        self,
        log_folder: str,
        ui_error_queue: "queue.Queue | None" = None,
    ) -> None:
        self._folder = Path(log_folder)
        self._ui_error_queue = ui_error_queue
        self._lock = threading.Lock()
        self._fh = None
        self._open_log_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, ev: dict) -> None:
        line = _format_line(ev)
        should_flush = ev.get("type", "") in _FLUSH_TYPES
        with self._lock:
            self._write_line(line, flush=should_flush)

    def write_error(self, msg: str, detail: object = "") -> None:
        ev = {
            "timestamp": _now(),
            "type": "ERROR",
            "key": "",
            "vk": "",
            "sc": "",
            "ext": 0,
            "inj": 0,
            "app": "KeyBlockDetective",
            "window": "",
            "user": _get_username(),
            "layout": "",
            "caps": "?",
            "num": "?",
            "scroll": "?",
            "sticky": "?",
            "filter": "?",
            "toggle": "?",
            "mods": "NONE",
            "note": f"{msg}: {detail}",
        }
        self.write(ev)

    def flush(self) -> None:
        with self._lock:
            if self._fh and not self._fh.closed:
                try:
                    self._fh.flush()
                except OSError as exc:
                    self._handle_os_error(exc)

    def close(self) -> None:
        with self._lock:
            if self._fh and not self._fh.closed:
                try:
                    self._fh.flush()
                    self._fh.close()
                except OSError as exc:
                    self._handle_os_error(exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _open_log_file(self) -> None:
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            fname = datetime.now().strftime("keyblock_%Y%m%d.log")
            fpath = self._folder / fname
            self._fh = open(fpath, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            _log.error("Cannot open log file: %s", exc)
            self._fh = None
            self._notify_ui_error(f"Cannot open log file: {exc}")

    def _write_line(self, line: str, flush: bool = False) -> None:
        if self._fh is None or self._fh.closed:
            return
        try:
            self._fh.write(line + "\n")
            if flush:
                self._fh.flush()
        except OSError as exc:
            self._handle_os_error(exc)

    def _handle_os_error(self, exc: OSError) -> None:
        _log.error("Log write error: %s", exc)
        self._notify_ui_error(f"Log write error: {exc}")

    def _notify_ui_error(self, msg: str) -> None:
        if self._ui_error_queue is not None:
            try:
                self._ui_error_queue.put_nowait({"type": "UI_ERROR", "msg": msg})
            except Exception:
                pass


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def _format_line(ev: dict) -> str:
    ts = ev.get("timestamp", _now())
    typ = ev.get("type", "")
    key = ev.get("key", "")
    vk = ev.get("vk", "?")
    sc = ev.get("sc", "?")
    vksc = f"VK:{vk!s:>3} SC:{sc!s:>3}"
    flags = f"FLAGS:EXT={ev.get('ext', 0)} INJ={ev.get('inj', 0)}"
    app = f"App:{ev.get('app', 'N/A')}"
    win_title = ev.get("window", "N/A")
    win = f'Window:"{win_title}"'
    user = f"User:{ev.get('user', 'N/A')}"
    layout = f"Layout:{ev.get('layout', 'N/A')}"
    state = (
        f"Caps:{ev.get('caps', '?')} "
        f"Num:{ev.get('num', '?')} "
        f"Scroll:{ev.get('scroll', '?')} "
        f"Sticky:{ev.get('sticky', '?')} "
        f"Filter:{ev.get('filter', '?')} "
        f"Toggle:{ev.get('toggle', '?')} "
        f"Mods:{ev.get('mods', 'NONE')}"
    )
    note = ev.get("note", "")
    return " | ".join([ts, typ, key, vksc, flags, app, win, user, layout, state, note])


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _get_username() -> str:
    import os
    return os.environ.get("USERNAME") or os.environ.get("USER") or "N/A"
