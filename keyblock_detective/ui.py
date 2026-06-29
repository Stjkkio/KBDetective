"""
tkinter-based main window + pystray tray icon for KeyBlock Detective.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

_log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 250


class MainWindow:
    def __init__(
        self,
        config,                     # config.Config
        event_queue: queue.Queue,   # raw keyboard/session events (write-only from UI side)
        ui_queue: queue.Queue,      # display dicts from worker
        start_cb,                   # callable() → start monitoring
        stop_cb,                    # callable() → stop monitoring
        recover_cb,                 # callable() → full_recovery(); returns list[tuple]
        stop_event: threading.Event,
    ) -> None:
        self._cfg = config
        self._eq = event_queue
        self._uq = ui_queue
        self._start_cb = start_cb
        self._stop_cb = stop_cb
        self._recover_cb = recover_cb
        self._stop_event = stop_event

        self._monitoring = False
        self._tray_icon = None

        self._root = tk.Tk()
        self._root.title("KeyBlock Detective")
        self._root.geometry(f"{config.window_width}x{config.window_height}")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._setup_tray()

        if config.start_minimized:
            self._root.withdraw()

        # Start the worker-output polling loop
        self._root.after(POLL_INTERVAL_MS, self._poll_ui_queue)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._root.mainloop()

    def show(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def append_line(self, text: str) -> None:
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, text + "\n")

        # Cap at max lines
        max_lines = self._cfg.ui_max_lines
        lines = int(self._log_text.index("end-1c").split(".")[0])
        if lines > max_lines:
            self._log_text.delete("1.0", f"{lines - max_lines}.0")

        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Top bar: Start/Stop + Unlock ──────────────────────────────
        top_frame = tk.Frame(self._root)
        top_frame.pack(fill=tk.X, padx=6, pady=4)

        self._start_btn = tk.Button(
            top_frame, text="Start monitoring",
            command=self._on_start, bg="#2e7d32", fg="white", width=18,
        )
        self._start_btn.pack(side=tk.LEFT, padx=2)

        self._stop_btn = tk.Button(
            top_frame, text="Stop monitoring",
            command=self._on_stop, state=tk.DISABLED, width=18,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=2)

        tk.Button(
            top_frame, text="Unlock keyboard now",
            command=self._on_recover, bg="#b71c1c", fg="white", width=20,
        ).pack(side=tk.LEFT, padx=8)

        tk.Button(
            top_frame, text="Open log folder",
            command=self._on_open_log_folder,
        ).pack(side=tk.RIGHT, padx=2)

        tk.Button(
            top_frame, text="Copy recent entries",
            command=self._on_copy_recent,
        ).pack(side=tk.RIGHT, padx=2)

        # ── Status panel ──────────────────────────────────────────────
        status_frame = tk.LabelFrame(self._root, text="System state", padx=4, pady=2)
        status_frame.pack(fill=tk.X, padx=6, pady=(0, 4))

        self._status_vars: dict[str, tk.StringVar] = {}
        indicators = ["Caps", "Num", "Scroll", "Sticky", "Filter", "Toggle", "Layout", "Mods"]
        for i, name in enumerate(indicators):
            var = tk.StringVar(value=f"{name}: ?")
            self._status_vars[name] = var
            tk.Label(status_frame, textvariable=var, relief=tk.SUNKEN,
                     width=16, anchor=tk.W).grid(row=0, column=i, padx=2)

        # ── Log viewer ────────────────────────────────────────────────
        log_frame = tk.Frame(self._root)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            state=tk.DISABLED,
            font=("Consolas", 9),
            wrap=tk.NONE,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # ── Status bar ────────────────────────────────────────────────
        self._status_bar = tk.Label(
            self._root, text="Ready", anchor=tk.W,
            relief=tk.SUNKEN, bd=1,
        )
        self._status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------
    # Tray icon
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        try:
            import pystray
            from PIL import Image

            icon_img = self._load_tray_image()

            menu = pystray.Menu(
                pystray.MenuItem("Show Window", self._tray_show),
                pystray.MenuItem("Stop Monitoring", self._on_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._on_quit),
            )
            self._tray_icon = pystray.Icon(
                "KeyBlockDetective",
                icon_img,
                "KeyBlock Detective",
                menu=menu,
            )
            t = threading.Thread(
                target=self._tray_icon.run,
                name="TrayThread",
                daemon=True,
            )
            t.start()
        except Exception as exc:
            _log.warning("Tray icon unavailable: %s", exc)

    def _load_tray_image(self):
        from PIL import Image

        icon_path = Path(__file__).parent / "assets" / "icon.png"
        if icon_path.exists():
            return Image.open(icon_path)
        return _make_tray_icon()

    def _tray_show(self, icon, item) -> None:
        self._root.after(0, self.show)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if not self._monitoring:
            self._monitoring = True
            self._start_btn.config(state=tk.DISABLED)
            self._stop_btn.config(state=tk.NORMAL)
            self._status_bar.config(text="Monitoring…")
            self._start_cb()

    def _on_stop(self) -> None:
        if self._monitoring:
            self._monitoring = False
            self._start_btn.config(state=tk.NORMAL)
            self._stop_btn.config(state=tk.DISABLED)
            self._status_bar.config(text="Stopped")
            self._stop_cb()

    def _on_recover(self) -> None:
        def _run() -> None:
            try:
                results = self._recover_cb()
                ok = all(r == "SUCCESS" for _, r in results)
                summary = "\n".join(f"  {n}: {r}" for n, r in results)
                status = "SUCCESS" if ok else "PARTIAL/FAILED"
                msg = f"Recovery result: {status}\n\n{summary}"
                self._root.after(0, lambda: messagebox.showinfo("Unlock keyboard", msg))

                # Log as AUTO_FIX MANUAL
                ui_ev = {
                    "type": "AUTO_FIX",
                    "note": f"Trigger:MANUAL Result:{status}",
                    "timestamp": _ts_now(),
                    "key": "full_recovery",
                    "vk": "", "sc": "", "ext": 0, "inj": 0,
                    "caps": "?", "num": "?", "scroll": "?",
                    "sticky": "?", "filter": "?", "toggle": "?",
                    "layout": "N/A", "mods": "NONE",
                    "user": "N/A", "app": "KeyBlockDetective", "window": "",
                }
                self._uq.put_nowait(ui_ev)
            except Exception as exc:
                self._root.after(0, lambda: messagebox.showerror("Error", str(exc)))

        threading.Thread(target=_run, daemon=True).start()

    def _on_open_log_folder(self) -> None:
        folder = self._cfg.log_folder
        if not folder:
            messagebox.showwarning("No folder", "Log folder not configured.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def _on_copy_recent(self) -> None:
        content = self._log_text.get("1.0", tk.END).strip()
        if content:
            self._root.clipboard_clear()
            self._root.clipboard_append(content)
            self._status_bar.config(text="Copied to clipboard")

    def _on_close(self) -> None:
        if self._tray_icon is not None:
            # Minimize to tray instead of closing
            self._root.withdraw()
        else:
            self._on_quit()

    def _on_quit(self, *_) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self._stop_event.set()
        self._root.after(300, self._root.destroy)

    # ------------------------------------------------------------------
    # Periodic UI refresh
    # ------------------------------------------------------------------

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                ev = self._uq.get_nowait()
                self._handle_ui_event(ev)
        except queue.Empty:
            pass
        finally:
            if not self._stop_event.is_set():
                self._root.after(POLL_INTERVAL_MS, self._poll_ui_queue)

    def _handle_ui_event(self, ev: dict) -> None:
        ev_type = ev.get("type", "")

        # Update status indicators if state fields present
        if "caps" in ev:
            self._status_vars["Caps"].set(f"Caps: {ev['caps']}")
        if "num" in ev:
            self._status_vars["Num"].set(f"Num: {ev['num']}")
        if "scroll" in ev:
            self._status_vars["Scroll"].set(f"Scroll: {ev['scroll']}")
        if "sticky" in ev:
            self._status_vars["Sticky"].set(f"Sticky: {ev['sticky']}")
        if "filter" in ev:
            self._status_vars["Filter"].set(f"Filter: {ev['filter']}")
        if "toggle" in ev:
            self._status_vars["Toggle"].set(f"Toggle: {ev['toggle']}")
        if "layout" in ev:
            self._status_vars["Layout"].set(f"Layout: {ev.get('layout', 'N/A')}")
        if "mods" in ev:
            self._status_vars["Mods"].set(f"Mods: {ev.get('mods', 'NONE')}")

        # Error notification
        if ev_type == "UI_ERROR":
            self._status_bar.config(text=f"Error: {ev.get('msg', '')}", fg="red")

        # Append to log viewer
        from event_logger import _format_line
        line = _format_line(ev)
        self.append_line(line)


# ------------------------------------------------------------------
# Tray icon fallback
# ------------------------------------------------------------------

def _make_tray_icon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 56, 56], outline=(0, 180, 255), width=3)
    d.text((18, 20), "KB", fill=(0, 180, 255))
    return img


def _ts_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
