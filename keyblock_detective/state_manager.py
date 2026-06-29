"""
Windows input-state reader and recovery actions for KeyBlock Detective.

All Win32 calls are wrapped so they can be mocked in tests.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os

_log = logging.getLogger(__name__)

# ── VK codes ──────────────────────────────────────────────────────────────
VK_CAPITAL = 0x14
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91

VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4   # L-Alt
VK_RMENU = 0xA5   # R-Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# ── SystemParametersInfo codes ────────────────────────────────────────────
SPI_GETSTICKYKEYS = 0x003A
SPI_SETSTICKYKEYS = 0x003B
SPI_GETFILTERKEYS = 0x0032
SPI_SETFILTERKEYS = 0x0033
SPI_GETTOGGLEKEYS = 0x0034
SPI_SETTOGGLEKEYS = 0x0035

SPIF_SENDCHANGE = 0x0002

# ── Accessibility structures ───────────────────────────────────────────────

class STICKYKEYS(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


class FILTERKEYS(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("iWaitMSec", ctypes.wintypes.DWORD),
        ("iDelayMSec", ctypes.wintypes.DWORD),
        ("iRepeatMSec", ctypes.wintypes.DWORD),
        ("iBounceMSec", ctypes.wintypes.DWORD),
    ]


class TOGGLEKEYS(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


# ── SendInput structures ───────────────────────────────────────────────────

KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]

    @property
    def ki(self) -> KEYBDINPUT:
        return self._input.ki


INPUT_KEYBOARD = 1


# ──────────────────────────────────────────────────────────────────────────
# State reader
# ──────────────────────────────────────────────────────────────────────────

def read_state() -> dict:
    """
    Return a snapshot of current input-related system state.
    All values are safe defaults if Win32 calls fail.
    """
    user32 = ctypes.windll.user32

    caps = _get_toggle_state(VK_CAPITAL)
    num = _get_toggle_state(VK_NUMLOCK)
    scroll = _get_toggle_state(VK_SCROLL)

    sticky = _get_sticky_keys_on()
    filt = _get_filter_keys_on()
    toggle_keys = _get_toggle_keys_on()

    layout = _get_keyboard_layout_name()
    mods = _get_active_modifiers()
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "N/A"

    hwnd = 0
    window_title = "N/A"
    app_name = "N/A"
    try:
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            window_title = buf.value or "N/A"
            app_name = _get_foreground_app_name(hwnd)
    except Exception:
        pass

    return {
        "caps": caps,
        "num": num,
        "scroll": scroll,
        "sticky": int(sticky),
        "filter": int(filt),
        "toggle": int(toggle_keys),
        "layout": layout,
        "mods": mods or "NONE",
        "user": user,
        "app": app_name,
        "window": window_title,
    }


# ──────────────────────────────────────────────────────────────────────────
# Recovery actions
# ──────────────────────────────────────────────────────────────────────────

def disable_sticky_keys() -> str:
    sk = STICKYKEYS()
    sk.cbSize = ctypes.sizeof(STICKYKEYS)
    sk.dwFlags = 0  # all flags cleared = feature off
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETSTICKYKEYS, ctypes.sizeof(STICKYKEYS), ctypes.byref(sk), SPIF_SENDCHANGE
    )
    result = "SUCCESS" if ok else "FAILED"
    _log.info("disable_sticky_keys → %s", result)
    return result


def disable_filter_keys() -> str:
    fk = FILTERKEYS()
    fk.cbSize = ctypes.sizeof(FILTERKEYS)
    fk.dwFlags = 0
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETFILTERKEYS, ctypes.sizeof(FILTERKEYS), ctypes.byref(fk), SPIF_SENDCHANGE
    )
    result = "SUCCESS" if ok else "FAILED"
    _log.info("disable_filter_keys → %s", result)
    return result


def disable_toggle_keys() -> str:
    tk = TOGGLEKEYS()
    tk.cbSize = ctypes.sizeof(TOGGLEKEYS)
    tk.dwFlags = 0
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETTOGGLEKEYS, ctypes.sizeof(TOGGLEKEYS), ctypes.byref(tk), SPIF_SENDCHANGE
    )
    result = "SUCCESS" if ok else "FAILED"
    _log.info("disable_toggle_keys → %s", result)
    return result


def release_stuck_modifiers() -> str:
    """Send KEYUP for every modifier VK to unstick held keys."""
    vks = [VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
           VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN]
    try:
        for vk in vks:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki.wVk = vk
            inp.ki.dwFlags = KEYEVENTF_KEYUP
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        _log.info("release_stuck_modifiers → SUCCESS")
        return "SUCCESS"
    except Exception as exc:
        _log.error("release_stuck_modifiers → FAILED: %s", exc)
        return "FAILED"


def full_recovery() -> list[tuple[str, str]]:
    """
    Run all recovery actions and return a list of (action_name, result) tuples.
    Each result is 'SUCCESS' or 'FAILED'.
    """
    steps = [
        ("disable_sticky_keys", disable_sticky_keys),
        ("disable_filter_keys", disable_filter_keys),
        ("disable_toggle_keys", disable_toggle_keys),
        ("release_stuck_modifiers", release_stuck_modifiers),
    ]
    results = []
    for name, fn in steps:
        try:
            result = fn()
        except Exception as exc:
            result = f"FAILED ({exc})"
        results.append((name, result))
    return results


# ──────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────

def _get_toggle_state(vk: int) -> int:
    try:
        state = ctypes.windll.user32.GetKeyState(vk)
        return 1 if (state & 0x0001) else 0
    except Exception:
        return 0


def _get_sticky_keys_on() -> bool:
    sk = STICKYKEYS()
    sk.cbSize = ctypes.sizeof(STICKYKEYS)
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETSTICKYKEYS, ctypes.sizeof(STICKYKEYS), ctypes.byref(sk), 0
        )
        return bool(sk.dwFlags & 0x00000001)  # SKF_STICKYKEYSON
    except Exception:
        return False


def _get_filter_keys_on() -> bool:
    fk = FILTERKEYS()
    fk.cbSize = ctypes.sizeof(FILTERKEYS)
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETFILTERKEYS, ctypes.sizeof(FILTERKEYS), ctypes.byref(fk), 0
        )
        return bool(fk.dwFlags & 0x00000001)  # FKF_FILTERKEYSON
    except Exception:
        return False


def _get_toggle_keys_on() -> bool:
    tk = TOGGLEKEYS()
    tk.cbSize = ctypes.sizeof(TOGGLEKEYS)
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETTOGGLEKEYS, ctypes.sizeof(TOGGLEKEYS), ctypes.byref(tk), 0
        )
        return bool(tk.dwFlags & 0x00000001)  # TKF_TOGGLEKEYSON
    except Exception:
        return False


def _get_keyboard_layout_name() -> str:
    try:
        buf = ctypes.create_unicode_buffer(9)
        ctypes.windll.user32.GetKeyboardLayoutNameW(buf)
        return buf.value or "N/A"
    except Exception:
        return "N/A"


def _get_active_modifiers() -> str:
    names = []
    pairs = [
        (VK_LSHIFT, "LSHIFT"), (VK_RSHIFT, "RSHIFT"),
        (VK_LCONTROL, "LCTRL"), (VK_RCONTROL, "RCTRL"),
        (VK_LMENU, "LALT"), (VK_RMENU, "RALT"),
        (VK_LWIN, "LWIN"), (VK_RWIN, "RWIN"),
    ]
    try:
        for vk, name in pairs:
            if ctypes.windll.user32.GetKeyState(vk) & 0x8000:
                names.append(name)
    except Exception:
        pass
    return "+".join(names) if names else ""


def _get_foreground_app_name(hwnd: int) -> str:
    try:
        import os as _os
        # win32process / win32api may not be available in tests
        import importlib
        w32p = importlib.import_module("win32process")
        w32a = importlib.import_module("win32api")
        w32c = importlib.import_module("win32con")
        _, pid = w32p.GetWindowThreadProcessId(hwnd)
        h = w32a.OpenProcess(w32c.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        exe = w32p.GetModuleFileNameEx(h, 0)
        return _os.path.basename(exe)
    except Exception:
        return "N/A"
