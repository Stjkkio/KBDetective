"""
Low-level keyboard hook (WH_KEYBOARD_LL) for KeyBlock Detective.

The callback is intentionally minimal: it only calls queue.put_nowait()
and returns CallNextHookEx immediately.  No I/O, no Win32 queries, no UI.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import queue
import threading
import time

_log = logging.getLogger(__name__)

# Windows message constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


_LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

_hook_handle: ctypes.c_void_p | None = None
_event_queue: queue.Queue | None = None
_hook_proc_ref = None  # keep a reference so GC doesn't collect it
_hook_thread: threading.Thread | None = None


def install(event_queue: queue.Queue) -> None:
    """Install the hook in a dedicated thread with its own message pump."""
    global _hook_thread

    _hook_thread = threading.Thread(
        target=_hook_thread_main,
        args=(event_queue,),
        name="KBHookThread",
        daemon=True,
    )
    _hook_thread.start()


def uninstall() -> None:
    """Uninstall the hook and signal the message pump to exit."""
    global _hook_handle
    if _hook_handle is not None:
        try:
            user32 = ctypes.windll.user32
            user32.UnhookWindowsHookEx(_hook_handle)
            _log.info("Keyboard hook uninstalled")
        except Exception as exc:
            _log.error("Failed to uninstall hook: %s", exc)
        finally:
            _hook_handle = None

    # Wake the message pump so its thread can exit
    if _hook_thread is not None:
        try:
            ctypes.windll.user32.PostThreadMessageW(
                ctypes.windll.kernel32.GetCurrentThreadId(), 0x0012, 0, 0  # WM_QUIT
            )
        except Exception:
            pass


def _hook_thread_main(event_queue: queue.Queue) -> None:
    """Run in its own OS thread; installs hook then pumps messages."""
    global _hook_handle, _event_queue, _hook_proc_ref

    _event_queue = event_queue

    callback = _LowLevelKeyboardProc(_ll_keyboard_proc)
    _hook_proc_ref = callback  # prevent GC

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    try:
        # WH_KEYBOARD_LL is a global low-level hook: hMod must be NULL (not the module handle)
        _hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, None, 0)
        if not _hook_handle:
            err = kernel32.GetLastError()
            _log.error("SetWindowsHookEx failed, error=%d", err)
            try:
                event_queue.put_nowait({
                    "type": "ERROR",
                    "note": f"Hook install failed (error {err})",
                    "ts": time.time(),
                })
            except queue.Full:
                pass
            return

        _log.info("Keyboard hook installed (handle=%s)", _hook_handle)

        # Standard Win32 message pump — required for LL hooks
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    finally:
        if _hook_handle is not None:
            user32.UnhookWindowsHookEx(_hook_handle)
            _hook_handle = None
            _log.info("Keyboard hook cleaned up in finally block")


def _ll_keyboard_proc(nCode: int, wParam: int, lParam: int) -> int:
    """Low-level keyboard hook callback — MUST be extremely fast."""
    if nCode >= 0 and _event_queue is not None:
        try:
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            _event_queue.put_nowait({
                "vk": kbd.vkCode,
                "sc": kbd.scanCode,
                "flags": kbd.flags,
                "time_ms": kbd.time,
                "transition": wParam,  # WM_KEYDOWN / WM_KEYUP / etc.
                "ts": time.time(),
            })
        except queue.Full:
            pass  # worker monitors backlog separately
        except Exception:
            pass  # never raise inside a hook callback

    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)
