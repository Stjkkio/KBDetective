"""
KeyBlock Detective — entry point.

Start order:
  1. Load / create config
  2. Open event logger
  3. Start SessionMonitor thread
  4. Start WorkerThread
  5. Install keyboard hook
  6. Run tkinter main loop (blocks until quit)

Shutdown (triggered by UI quit):
  1. _stop_event set → WorkerThread drains queue → logger flushes
  2. SessionMonitor receives WM_QUIT
  3. KeyboardHook uninstalled
  4. pystray icon stopped (handled by MainWindow)
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger(__name__)


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main() -> None:
    # ── Config ────────────────────────────────────────────────────────
    from config import Config
    cfg = Config.get_or_create()

    if not is_elevated():
        _log.warning(
            "Running without administrator privileges.  "
            "The hook will still work, but some recovery actions may be limited."
        )

    # ── Queues and events ─────────────────────────────────────────────
    event_queue: queue.Queue = queue.Queue(maxsize=cfg.queue_max_size)
    ui_queue: queue.Queue = queue.Queue(maxsize=500)
    _stop_event = threading.Event()

    # ── Logger ────────────────────────────────────────────────────────
    from event_logger import EventLogger
    logger = EventLogger(cfg.log_folder, ui_error_queue=ui_queue)

    # ── Session monitor ───────────────────────────────────────────────
    from session_monitor import SessionMonitor
    session_monitor = SessionMonitor(event_queue)
    session_monitor.start()
    session_monitor.wait_ready(timeout=2.0)

    # ── Worker thread ─────────────────────────────────────────────────
    import state_manager
    from worker import WorkerThread
    worker = WorkerThread(
        event_queue=event_queue,
        ui_queue=ui_queue,
        logger=logger,
        state_reader=state_manager.read_state,
        config=cfg,
        stop_event=_stop_event,
    )
    worker.start()

    # ── Keyboard hook ─────────────────────────────────────────────────
    import keyboard_hook
    hook_active = False

    def start_monitoring() -> None:
        nonlocal hook_active
        if not hook_active:
            try:
                keyboard_hook.install(event_queue)
                hook_active = True
                _log.info("Keyboard hook installed")
            except Exception as exc:
                _log.error("Failed to install keyboard hook: %s", exc)
                logger.write_error("Hook install failed", exc)

    def stop_monitoring() -> None:
        nonlocal hook_active
        if hook_active:
            keyboard_hook.uninstall()
            hook_active = False
            _log.info("Keyboard hook uninstalled")

    # ── UI ────────────────────────────────────────────────────────────
    from ui import MainWindow
    window = MainWindow(
        config=cfg,
        event_queue=event_queue,
        ui_queue=ui_queue,
        start_cb=start_monitoring,
        stop_cb=stop_monitoring,
        recover_cb=state_manager.full_recovery,
        stop_event=_stop_event,
    )

    # ── Run (blocks until window is closed) ───────────────────────────
    _log.info("Starting UI main loop")
    window.run()

    # ── Shutdown sequence ─────────────────────────────────────────────
    _log.info("Shutting down…")

    stop_monitoring()

    # Signal worker to drain and exit
    _stop_event.set()
    worker.join(timeout=5.0)

    # Flush and close log
    logger.close()

    # Stop session monitor
    session_monitor.stop()
    session_monitor.join(timeout=2.0)

    _log.info("KeyBlock Detective exited cleanly")


if __name__ == "__main__":
    main()
