"""
bubble.py – Floating speech/thought bubble window.

Ported from the GTK version. The window itself is now a frameless,
translucent, always-on-top, fully click-through QWidget (Qt.Tool +
WA_TranslucentBackground + WA_TransparentForMouseEvents — the Qt
equivalent of the old empty input-shape region: nothing in this window
is ever clickable). Drawing moved from Cairo to QPainter; the actual
bubble geometry/wrapping/positioning logic is unchanged from the
original.
"""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPainterPath, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget

from config import BUBBLE_W


class _BubbleWindow(QWidget):
    def __init__(self, manager: 'BubbleManager'):
        super().__init__()
        self._mgr = manager
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        mgr = self._mgr
        if mgr.thought_ticks > 0 and mgr.thought_text:
            mgr._draw_thought(p, self.width(), self.height())
        elif mgr.speech_ticks > 0 and mgr.speech_text:
            mgr._draw_speech(p, self.width(), self.height())
        p.end()


class BubbleManager:
    """Manages the floating speech/thought bubble window."""

    def __init__(self):
        self.speech_text   = ''
        self.speech_ticks   = 0
        self.speech_total  = 0
        self.thought_text  = ''
        self.thought_ticks = 0
        self.thought_total = 0
        self._side         = 'left'
        self._font         = QFont('Sans', 11, QFont.Bold)

        self._win = _BubbleWindow(self)
        self._win.resize(BUBBLE_W, 80)

    @property
    def visible(self) -> bool:
        return self.speech_ticks > 0 or self.thought_ticks > 0

    def say(self, text: str, secs: float) -> None:
        self.speech_text   = text
        self.speech_ticks  = max(1, int(secs * 20))
        self.speech_total  = self.speech_ticks
        self.thought_ticks = 0

    def think(self, text: str, secs: float) -> None:
        self.thought_text  = text
        self.thought_ticks = max(1, int(secs * 20))
        self.thought_total = self.thought_ticks
        self.speech_ticks  = 0

    def tick(self) -> tuple:
        """Decrement counters. Returns (was_visible, is_visible)."""
        was = self.visible
        if self.speech_ticks  > 0: self.speech_ticks  -= 1
        if self.thought_ticks > 0: self.thought_ticks -= 1
        return was, self.visible

    def position(self, wx: int, wy: int, win_w: int, win_h: int,
                 screen_w: int, screen_h: int,
                 panel_open: bool, panel_rect) -> None:
        if not self.visible:
            self._win.hide()
            return

        text = self.thought_text if self.thought_ticks > 0 else self.speech_text
        bw   = BUBBLE_W
        bh   = self._measure_height(text, bw - 26)

        self._win.resize(bw, bh)

        panel_left = panel_right = None
        if panel_open and panel_rect:
            panel_left  = panel_rect[0]
            panel_right = panel_rect[0] + panel_rect[2]

        bx   = wx - bw - 4
        side = 'left'

        left_blocked = bx < 0
        if not left_blocked and panel_left is not None:
            if (bx + bw) > panel_left and bx < panel_right:
                left_blocked = True

        if left_blocked:
            bx   = wx + win_w + 4
            side = 'right'
            if panel_left is not None and (bx + bw) > panel_left and bx < panel_right:
                bx   = panel_left - bw - 8
                side = 'left'

        bx = max(0, min(bx, screen_w - bw - 4))
        by = wy + (win_h - bh) // 2
        by = max(0, min(by, screen_h - bh - 4))

        self._side = side
        self._win.move(int(bx), int(by))
        self._win.show()
        self._win.raise_()
        self._win.update()

    def hide(self) -> None:
        self._win.hide()

    def destroy(self) -> None:
        self._win.close()

    # ── Measuring / wrapping ──

    def _measure_height(self, text: str, max_w: float) -> int:
        lines = self._wrap_text(text, max_w)
        return max(44, len(lines) * 17 + 26)

    def _wrap_text(self, text: str, max_w: float) -> list:
        fm = QFontMetrics(self._font)
        words = text.split()
        lines, line = [], ''
        for w in words:
            test = (line + ' ' + w).strip()
            if fm.horizontalAdvance(test) > max_w and line:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)
        return lines

    def _alpha(self, ticks: int, total: int) -> float:
        fade_in  = min(1.0, ticks / 10)
        fade_out = min(1.0, (total - ticks) / 8) if (total - ticks) >= 8 else 1.0
        return min(fade_in, fade_out)

    # ── Drawing ──

    def _draw_thought(self, p: QPainter, bw: int, bh: int) -> None:
        alpha = self._alpha(self.thought_ticks, self.thought_total)
        side  = self._side
        CONN  = 20

        lines = self._wrap_text(self.thought_text, bw - 20)
        lh    = 16
        bh2   = len(lines) * lh + 22

        if side == 'left':
            bx2   = 6
            bw2   = bw - CONN - 6
            mid_y = bh // 2
            dots  = [(bw2 + 8, mid_y + 4, 4), (bw2 + 14, mid_y, 6), (bw2 + CONN, mid_y - 4, 5)]
        else:
            bx2   = CONN
            bw2   = bw - CONN - 6
            mid_y = bh // 2
            dots  = [(CONN - 8, mid_y + 4, 4), (CONN - 14, mid_y, 6), (0, mid_y - 4, 5)]

        by2 = max(4, (bh - bh2) // 2)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, int(alpha * 0.9 * 255)))
        for ox, oy, r in dots:
            p.drawEllipse(QPointF(ox, oy), r, r)

        path = QPainterPath()
        path.addRoundedRect(bx2, by2, bw2, bh2, 16, 16)
        p.setBrush(QColor(255, 255, 255, int(alpha * 0.93 * 255)))
        p.setPen(QColor(0, 0, 0, int(0.15 * alpha * 255)))
        p.drawPath(path)

        p.setFont(self._font)
        p.setPen(QColor(int(0.1 * 255), int(0.1 * 255), int(0.15 * 255), int(alpha * 255)))
        fm = QFontMetrics(self._font)
        for i, l in enumerate(lines):
            tw = fm.horizontalAdvance(l)
            p.drawText(QPointF(bx2 + (bw2 - tw) / 2, by2 + 16 + i * lh + fm.ascent() * 0.3), l)

    def _draw_speech(self, p: QPainter, bw: int, bh: int) -> None:
        alpha = self._alpha(self.speech_ticks, self.speech_total)
        side  = self._side
        TAIL  = 16

        lines = self._wrap_text(self.speech_text, bw - 26)
        lh    = 16
        bh2   = len(lines) * lh + 18

        if side == 'left':
            bx2  = 4
            bw2  = bw - TAIL - 8
            by2  = max(4, (bh - bh2) // 2)
            ty   = by2 + bh2 // 2
            tail = [(bx2 + bw2, ty - 5), (bx2 + bw2 + TAIL, ty), (bx2 + bw2, ty + 5)]
        else:
            bx2  = TAIL + 4
            bw2  = bw - TAIL - 8
            by2  = max(4, (bh - bh2) // 2)
            ty   = by2 + bh2 // 2
            tail = [(bx2, ty - 5), (bx2 - TAIL, ty), (bx2, ty + 5)]

        path = QPainterPath()
        path.addRoundedRect(bx2, by2, bw2, bh2, 10, 10)
        p.setBrush(QColor(255, 255, 255, int(alpha * 0.95 * 255)))
        p.setPen(QColor(0, 0, 0, int(0.15 * alpha * 255)))
        p.drawPath(path)

        tail_path = QPainterPath()
        tail_path.moveTo(*tail[0])
        tail_path.lineTo(*tail[1])
        tail_path.lineTo(*tail[2])
        tail_path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, int(alpha * 0.95 * 255)))
        p.drawPath(tail_path)

        p.setFont(self._font)
        p.setPen(QColor(int(0.1 * 255), int(0.1 * 255), int(0.15 * 255), int(alpha * 255)))
        fm = QFontMetrics(self._font)
        for i, l in enumerate(lines):
            tw = fm.horizontalAdvance(l)
            p.drawText(QPointF(bx2 + (bw2 - tw) / 2, by2 + 14 + i * lh + fm.ascent() * 0.3), l)
