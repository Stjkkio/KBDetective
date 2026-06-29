"""
Session monitor for KeyBlock Detective.

Creates a hidden Win32 window, registers for WTS session notifications,
and enqueues SESSION LOCK/UNLOCK events.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

_log = logging.getLogger(__name__)

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

_CLASS_NAME = "KeyBlockSessionMonitor"


class SessionMonitor(threading.Thread):
    """
    Runs a hidden HWND that receives WM_WTSSESSION_CHANGE and enqueues
    SESSION events.  Stopped by calling stop(), which posts WM_QUIT.
    """

    def __init__(self, event_queue: queue.Queue) -> None:
        super().__init__(name="SessionMonitorThread", daemon=True)
        self._queue = event_queue
        self._hwnd: int | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        try:
            import win32gui
            import win32ts
            import win32api
        except ImportError:
            _log.warning("pywin32 not available — session monitoring disabled")
            self._ready.set()
            return

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.lpszClassName = _CLASS_NAME
        wc.hInstance = win32api.GetModuleHandle(None)

        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass  # already registered

        try:
            self._hwnd = win32gui.CreateWindowEx(
                0, _CLASS_NAME, "", 0,
                0, 0, 0, 0,
                None, None, wc.hInstance, None,
            )
            win32ts.WTSRegisterSessionNotification(self._hwnd, NOTIFY_FOR_THIS_SESSION)
            _log.info("Session monitor registered (hwnd=%s)", self._hwnd)
        except Exception as exc:
            _log.error("Session monitor setup failed: %s", exc)
            self._ready.set()
            return

        self._thread_id = win32api.GetCurrentThreadId()
        self._ready.set()

        try:
            win32gui.PumpMessages()
        except Exception as exc:
            _log.error("Session monitor pump error: %s", exc)
        finally:
            if self._hwnd:
                try:
                    win32ts.WTSUnRegisterSessionNotification(self._hwnd)
                    win32gui.DestroyWindow(self._hwnd)
                except Exception:
                    pass
            try:
                win32gui.UnregisterClass(_CLASS_NAME, wc.hInstance)
            except Exception:
                pass

    def stop(self) -> None:
        """Signal the message pump to stop."""
        try:
            import win32api
            import win32con
            if self._thread_id:
                win32api.PostThreadMessage(self._thread_id, win32con.WM_QUIT, 0, 0)
        except Exception as exc:
            _log.debug("SessionMonitor.stop() error: %s", exc)

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_WTSSESSION_CHANGE:
            detail: str | None = None
            if wparam == WTS_SESSION_LOCK:
                detail = "LOCK"
            elif wparam == WTS_SESSION_UNLOCK:
                detail = "UNLOCK"

            if detail is not None:
                try:
                    self._queue.put_nowait({
                        "type": "SESSION",
                        "detail": detail,
                        "ts": time.time(),
                    })
                except queue.Full:
                    _log.warning("Event queue full — dropped SESSION %s", detail)

        try:
            import win32gui
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
        except Exception:
            return 0
