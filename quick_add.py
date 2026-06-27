"""
quick_add.py - Floating quick-add entry window (used by BuddyApp hotkey and CLI).

Ported from the GTK version. Same approach as the rest of this package:
behavior unchanged (Enter=save, Escape=cancel, focus-out=cancel, same
parsing via utils._parse_quick_add), only the window/widget APIs are Qt.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QLineEdit, QVBoxLayout

from utils import _parse_quick_add


class _QuickAddEntry(QLineEdit):
    """QLineEdit that also treats Escape as a cancel key (Enter is handled
    separately via the returnPressed signal, same as GTK's 'activate')."""

    def __init__(self, on_escape):
        super().__init__()
        self._on_escape = on_escape

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_escape()
            return
        super().keyPressEvent(event)


class _QuickAddWindow(QWidget):
    """Frameless, translucent, always-on-top popup - closes itself (and
    calls on_close/quit_on_done) on save, cancel, or losing focus."""

    def __init__(self, screen_w, screen_h, on_save, on_close, quit_on_done):
        super().__init__()
        self._on_save     = on_save
        self._on_close     = on_close
        self._quit_on_done = quit_on_done
        self._done_called  = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(360, 48)
        self.move(screen_w // 2 - 180, screen_h // 2 - 24)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.entry = _QuickAddEntry(on_escape=lambda: self._done(False))
        self.entry.setPlaceholderText(
            "! high  ~ low  /today /tmr /fri #tag  Enter=save  Esc=cancel")
        self.entry.returnPressed.connect(lambda: self._done(True))
        layout.addWidget(self.entry)

    def _done(self, save: bool) -> None:
        if self._done_called:
            return
        self._done_called = True
        if save:
            raw = self.entry.text().strip()
            if raw:
                parsed = _parse_quick_add(raw)
                text   = parsed['text']
                if text:
                    self._on_save(parsed, text)
        self.close()
        self._on_close()
        if self._quit_on_done:
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()

    def focusOutEvent(self, event) -> None:
        self._done(False)
        super().focusOutEvent(event)


def build_quick_add_window(
    screen_w: int,
    screen_h: int,
    on_save,
    on_close,
    quit_on_done: bool = False,
) -> None:
    """
    Build and show the floating quick-add entry window.
    Used by both BuddyApp._quick_add_task() and the --quick-add CLI path.

    Args:
        screen_w / screen_h : screen dimensions for centering the window.
        on_save             : called with (parsed_dict, text_str) when the user
                              confirms a non-empty entry.
        on_close            : called whenever the window is dismissed (save or
                              cancel), so the caller can clear its open-flag.
        quit_on_done        : if True, quits the QApplication after close (used
                              in the standalone --quick-add CLI path).
    """
    win = _QuickAddWindow(screen_w, screen_h, on_save, on_close, quit_on_done)
    win.show()
    win.entry.setFocus()
    # Keep a reference alive on the QApplication instance - an unparented
    # top-level QWidget with no other Python reference can otherwise be
    # garbage-collected the moment this function returns.
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        if not hasattr(app, '_quick_add_refs'):
            app._quick_add_refs = []
        app._quick_add_refs.append(win)
