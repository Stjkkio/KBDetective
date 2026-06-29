"""INI-based configuration for KeyBlock Detective."""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

CONFIG_FILENAME = "keyblock_detective.ini"

DEFAULTS: dict[str, dict[str, str]] = {
    "general": {
        "log_folder": "",
        "auto_recovery": "true",
        "start_minimized": "false",
        "preferred_layout": "",
    },
    "ui": {
        "max_log_lines": "1000",
        "window_width": "900",
        "window_height": "650",
    },
    "advanced": {
        "queue_max_size": "2000",
        "backlog_warn_threshold": "500",
        "modifier_hold_threshold_sec": "3.0",
        "rapid_toggle_count": "3",
        "rapid_toggle_window_sec": "2.0",
    },
}


def _default_config_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home())
    else:
        base = Path.home() / ".config"
    return Path(base) / "KeyBlockDetective" / CONFIG_FILENAME


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_config_path()
        self._parser = configparser.ConfigParser()
        self._load_defaults()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _load_defaults(self) -> None:
        for section, values in DEFAULTS.items():
            self._parser[section] = values

    def load(self) -> None:
        """Read from disk; missing file is silently ignored (defaults apply)."""
        if self.path.exists():
            self._parser.read(self.path, encoding="utf-8")

    def save(self) -> None:
        """Persist current config to disk, creating parent dirs if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            self._parser.write(fh)

    @classmethod
    def get_or_create(cls, path: Path | None = None) -> "Config":
        """
        Load existing config or create a new one.
        On first run (no log_folder set), open a folder-picker dialog.
        """
        cfg = cls(path)
        cfg.load()

        if not cfg.log_folder:
            chosen = _ask_log_folder()
            if chosen:
                cfg.log_folder = chosen
            else:
                # Fall back to a sensible default next to the config file
                cfg.log_folder = str(cfg.path.parent / "logs")
            cfg.save()

        return cfg

    # ------------------------------------------------------------------
    # Typed getters / setters — [general]
    # ------------------------------------------------------------------

    @property
    def log_folder(self) -> str:
        return self._parser.get("general", "log_folder")

    @log_folder.setter
    def log_folder(self, value: str) -> None:
        self._parser.set("general", "log_folder", value)

    @property
    def auto_recovery(self) -> bool:
        return self._parser.getboolean("general", "auto_recovery")

    @auto_recovery.setter
    def auto_recovery(self, value: bool) -> None:
        self._parser.set("general", "auto_recovery", str(value).lower())

    @property
    def start_minimized(self) -> bool:
        return self._parser.getboolean("general", "start_minimized")

    @start_minimized.setter
    def start_minimized(self, value: bool) -> None:
        self._parser.set("general", "start_minimized", str(value).lower())

    @property
    def preferred_layout(self) -> str:
        return self._parser.get("general", "preferred_layout")

    @preferred_layout.setter
    def preferred_layout(self, value: str) -> None:
        self._parser.set("general", "preferred_layout", value)

    # ------------------------------------------------------------------
    # Typed getters / setters — [ui]
    # ------------------------------------------------------------------

    @property
    def ui_max_lines(self) -> int:
        return self._parser.getint("ui", "max_log_lines")

    @ui_max_lines.setter
    def ui_max_lines(self, value: int) -> None:
        self._parser.set("ui", "max_log_lines", str(value))

    @property
    def window_width(self) -> int:
        return self._parser.getint("ui", "window_width")

    @property
    def window_height(self) -> int:
        return self._parser.getint("ui", "window_height")

    # ------------------------------------------------------------------
    # Typed getters / setters — [advanced]
    # ------------------------------------------------------------------

    @property
    def queue_max_size(self) -> int:
        return self._parser.getint("advanced", "queue_max_size")

    @property
    def backlog_warn_threshold(self) -> int:
        return self._parser.getint("advanced", "backlog_warn_threshold")

    @property
    def modifier_hold_threshold_sec(self) -> float:
        return self._parser.getfloat("advanced", "modifier_hold_threshold_sec")

    @property
    def rapid_toggle_count(self) -> int:
        return self._parser.getint("advanced", "rapid_toggle_count")

    @property
    def rapid_toggle_window_sec(self) -> float:
        return self._parser.getfloat("advanced", "rapid_toggle_window_sec")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _ask_log_folder() -> str:
    """
    Open a tkinter folder-picker dialog and return the chosen path.
    Returns empty string if the user cancels or tkinter is unavailable.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(
            title="KeyBlock Detective — choose log folder",
            parent=root,
        )
        root.destroy()
        return chosen or ""
    except Exception:
        return ""
