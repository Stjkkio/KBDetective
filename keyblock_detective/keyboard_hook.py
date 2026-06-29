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
WM_QUIT = 0x0012


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.c_ulong),
        ("scanCode",    ctypes.c_ulong),
        ("flags",       ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        # dwExtraInfo is ULONG_PTR — use c_uint64 so the struct is sized correctly on 64-bit
        ("dwExtraInfo", ctypes.c_uint64),
    ]


# LRESULT is pointer-sized (64-bit on 64-bit Windows)
_LRESULT = ctypes.c_longlong

_LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    _LRESULT,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

# ── Win32 function signatures (set once at module level) ──────────────────
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    _LowLevelKeyboardProc,
    ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.DWORD,
]
_user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]

_user32.CallNextHookEx.restype = _LRESULT
_user32.CallNextHookEx.argtypes = [
    ctypes.wintypes.HHOOK,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
_user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

# ── Module-level state ────────────────────────────────────────────────────
_hook_handle = None
_event_queue: queue.Queue | None = None
_hook_proc_ref = None       # prevent GC from collecting the callback
_hook_thread: threading.Thread | None = None
_hook_thread_os_id: int = 0  # OS thread ID of the hook thread (for PostThreadMessage)


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
    """Uninstall the hook and signal the hook thread's message pump to exit."""
    global _hook_handle

    if _hook_handle is not None:
        try:
            _user32.UnhookWindowsHookEx(_hook_handle)
            _log.info("Keyboard hook uninstalled")
        except Exception as exc:
            _log.error("Failed to uninstall hook: %s", exc)
        finally:
            _hook_handle = None

    # Post WM_QUIT to the hook thread's message pump (NOT the calling thread)
    if _hook_thread_os_id:
        try:
            _user32.PostThreadMessageW(_hook_thread_os_id, WM_QUIT, 0, 0)
        except Exception as exc:
            _log.debug("PostThreadMessage error: %s", exc)


def _hook_thread_main(event_queue: queue.Queue) -> None:
    """Run in its own OS thread; installs hook then pumps messages."""
    global _hook_handle, _event_queue, _hook_proc_ref, _hook_thread_os_id

    _event_queue = event_queue
    _hook_thread_os_id = _kernel32.GetCurrentThreadId()

    callback = _LowLevelKeyboardProc(_ll_keyboard_proc)
    _hook_proc_ref = callback  # prevent GC

    try:
        # WH_KEYBOARD_LL is a global low-level hook: hMod must be NULL
        _hook_handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, None, 0)
        if not _hook_handle:
            err = _kernel32.GetLastError()
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

        # Message pump — required for LL hooks to fire.
        # GetMessageW returns 0 on WM_QUIT, -1 on error; both exit the loop.
        msg = ctypes.wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    finally:
        if _hook_handle is not None:
            _user32.UnhookWindowsHookEx(_hook_handle)
            _hook_handle = None
            _log.info("Keyboard hook cleaned up in finally block")


def _ll_keyboard_proc(nCode: int, wParam: int, lParam: int) -> int:
    """
    Low-level keyboard hook callback — MUST return in well under 300 ms.
    Only queue.put_nowait() is called; no I/O, no Win32 queries, no UI.
    """
    if nCode >= 0 and _event_queue is not None:
        try:
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            _event_queue.put_nowait({
                "vk": kbd.vkCode,
                "sc": kbd.scanCode,
                "flags": kbd.flags,
                "time_ms": kbd.time,
                "transition": wParam,
                "ts": time.time(),
            })
        except queue.Full:
            pass  # worker monitors backlog separately
        except Exception:
            pass  # never raise inside a hook callback

    # Always call the next hook — returning without this would swallow keystrokes
    return _user32.CallNextHookEx(None, nCode, wParam, lParam)
