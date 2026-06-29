# KeyBlock Detective

A Windows desktop diagnostic utility that captures low-level keyboard events
and session transitions to identify what causes keyboard lock or remapping.

## Quick start

```
pip install -r requirements.txt
python main.py
```

On first run a folder picker dialog asks where to save log files.

## Requirements

- Windows 10/11 (the `WH_KEYBOARD_LL` hook and `WTSRegisterSessionNotification`
  are Windows-only)
- Python 3.10+
- Packages: `pywin32`, `pystray`, `Pillow`

## Log format

One pipe-delimited line per event:

```
TIMESTAMP | TYPE | KEY | VK:xx SC:xx | FLAGS:EXT=x INJ=x | App:name | Window:"title" | User:name | Layout:name | Caps:x Num:x Scroll:x Sticky:x Filter:x Toggle:x Mods:... | Note
```

Event types: `KEY_DOWN`, `KEY_UP`, `SESSION`, `STATE_CHANGE`, `AUTO_FIX`, `ERROR`

## Pattern detection

| Pattern | Note field |
|---|---|
| Shift pressed 5× in a row | `KEY_COMBO SHIFT×5` |
| Alt held + Shift down | `KEY_COMBO ALT+SHIFT` |
| Ctrl held + Shift down | `KEY_COMBO CTRL+SHIFT` |
| Ctrl held + Alt down | `KEY_COMBO CTRL+ALT` |
| Modifier held > 3 s | `STATE_CHANGE MODIFIER_HOLD <VK> <duration>s` |
| Same lock key toggled ≥ 3× in 2 s | `STATE_CHANGE RAPID_<KEY>_TOGGLE` |
| Keyboard layout changes | `STATE_CHANGE LAYOUT_CHANGE old→new` |
| Accessibility feature toggled on | `STATE_CHANGE STICKY/FILTER/TOGGLE_KEYS ON` |

## Recovery

- **"Unlock keyboard now"** button runs `full_recovery()`:
  disables Sticky/Filter/Toggle Keys and sends KEYUP for all modifier VKs.
- Automatic recovery fires on SHIFT×5 and accessibility-feature activation
  (configurable via `auto_recovery = false` in INI).

## Configuration

`%APPDATA%\KeyBlockDetective\keyblock_detective.ini`

```ini
[general]
log_folder = C:\Users\...\AppData\Local\KeyBlockDetective\logs
auto_recovery = true
start_minimized = false
preferred_layout =

[ui]
max_log_lines = 1000
window_width = 900
window_height = 650

[advanced]
queue_max_size = 2000
backlog_warn_threshold = 500
modifier_hold_threshold_sec = 3.0
rapid_toggle_count = 3
rapid_toggle_window_sec = 2.0
```

## Tests

```
pip install -r requirements-dev.txt
pytest keyblock_detective/tests/
```

Tests mock all Win32 calls so they run on any platform.

## Privacy notice

KeyBlock Detective records **virtual key codes** (numeric identifiers),
scan codes, modifier states, and system metadata — **not** reconstructed
free-text or passwords.  Log files are stored only locally in the folder
you choose at first run.

## Optional packaging

```
pip install pyinstaller
pyinstaller --onefile --windowed --icon=assets/icon.ico \
            --add-data "assets;assets" main.py --name KeyBlockDetective
```
