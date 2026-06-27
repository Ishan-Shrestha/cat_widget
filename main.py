#!/usr/bin/env python3
"""
main.py – Entry point for Buddy (PySide6 / Qt port).

Usage:
    python3 main.py            # launch the cat widget
    python3 main.py --quick-add  # open the quick-add popup only (for hotkey binding)
"""

import json
import sys

from PySide6.QtWidgets import QApplication

from config import DATA_FILE
from data_store import _atomic_write, _load_json
from cat_controller import BuddyApp
from quick_add import build_quick_add_window
from utils import _build_todo_from_parsed, _parse_quick_add


def _run_quick_add(app: QApplication) -> None:
    """Standalone quick-add popup — for compositor hotkey binding."""
    data = _load_json(DATA_FILE, {'todos': []})

    try:
        from PySide6.QtGui import QGuiApplication
        geo    = QGuiApplication.primaryScreen().geometry()
        sw, sh = geo.width(), geo.height()
    except Exception:
        sw, sh = 1920, 1080

    def _on_save(parsed: dict, text: str) -> None:
        todo = _build_todo_from_parsed(parsed, text)
        data.setdefault('todos', []).insert(0, todo)
        _atomic_write(DATA_FILE, json.dumps(data))

    build_quick_add_window(
        screen_w=sw, screen_h=sh,
        on_save=_on_save, on_close=lambda: None,
        quit_on_done=True,
    )
    app.exec()


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if '--quick-add' in sys.argv:
        _run_quick_add(app)
    else:
        buddy = BuddyApp()
        app.exec()


if __name__ == '__main__':
    main()
