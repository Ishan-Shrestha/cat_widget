"""
panel_manager.py - Panel popup window with Pomo, Todos, Habits, and Stats tabs.

Ported from the GTK version. Same approach as cat_controller.py and
bubble.py: every bit of business logic (sorting, streak calc, economy
hooks, habit log math, todo filtering) is unchanged. What changed:

  - Gtk.Window + Gtk.DrawingArea -> QWidget (frameless/translucent/Tool),
    paintEvent does the drawing
  - Cairo drawing        -> QPainter (helpers below: _rrect/_divider/_qc
    in utils.py, plus a small arc-path helper here for the pomo ring)
  - Gtk.Dialog popups     -> QDialog (QLineEdit/QComboBox/QSpinBox etc.)
  - GTK signals           -> Qt mouse/wheel/key/focus event handlers

One deliberate simplification: the GTK version's add/edit-todo dialog
built a fully custom calendar + AM/PM spinner column by hand (~200
lines) because GTK has no single built-in date+time widget. Qt does
(QDateTimeEdit with a calendar popup), so that dialog uses it instead -
same feature (pick a due date and time), far less code, and it's a
well-tested native widget rather than a hand-rolled one.
"""

import math
import random
import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QPointF, QRectF, QDateTime
from PySide6.QtGui import QPainter, QPainterPath, QPen, QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QLabel, QLineEdit, QComboBox, QSpinBox,
    QDialogButtonBox, QTextEdit, QDateTimeEdit, QMessageBox,
)

from config import (
    PANEL_W, PANEL_H, BASE, MAUVE, OVERLAY, TEXT, SUBTEXT, SURFACE,
    RED, GREEN, YELLOW, SKY, TEAL, PRIO_COLOR, TAG_COLORS,
    RECUR_NONE, RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY,
)
from utils import (
    _rrect, _divider, _qc, _days_until, _get_period_key,
    _extract_tags, _strip_tags, _tag_color,
    _spawn_recur_task, _build_todo_from_parsed, _parse_quick_add,
    _play_chime, _notify,
)
from data_store import DataStore
from bubble import BubbleManager
from economy import Economy
from glib_compat import GLib


def _set_font(p: QPainter, size: float, bold: bool = False) -> None:
    p.setFont(QFont('Sans', int(size), QFont.Bold if bold else QFont.Normal))


def _text_w(p: QPainter, text: str) -> float:
    return p.fontMetrics().horizontalAdvance(text)


def _fill_rrect(p: QPainter, x, y, w, h, r, color: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(_rrect(x, y, w, h, r))


def _stroke_rrect(p: QPainter, x, y, w, h, r, color: QColor, width: float = 1) -> None:
    pen = QPen(color); pen.setWidthF(width)
    p.setPen(pen); p.setBrush(Qt.NoBrush)
    p.drawPath(_rrect(x, y, w, h, r))


def _fill_circle(p: QPainter, cx, cy, r, color: QColor) -> None:
    p.setPen(Qt.NoPen); p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), r, r)


def _stroke_circle(p: QPainter, cx, cy, r, color: QColor, width: float = 1) -> None:
    pen = QPen(color); pen.setWidthF(width)
    p.setPen(pen); p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)


def _arc_path(cx, cy, r, a1: float, a2: float) -> QPainterPath:
    start_deg = -math.degrees(a1)
    sweep_deg = -math.degrees(a2 - a1)
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, sweep_deg)
    return path


def _draw_text(p: QPainter, x, y, text: str, color: QColor, size: float, bold: bool = False) -> None:
    _set_font(p, size, bold)
    p.setPen(color)
    p.drawText(QPointF(x, y), text)


class PanelManager(QWidget):
    """Owns the panel popup window and all drawing/click logic within it."""

    def __init__(self, store: DataStore, bubble: BubbleManager, app):
        super().__init__()
        self._store  = store
        self._bubble = bubble
        self._app    = app

        self.open        = False
        self.active_tab  = 'pomo'
        self.dialog_open = False

        self.active_tag_filter = None
        self._tag_filter_rects = []

        self.pomo_running    = False
        self.pomo_is_break   = False
        self.pomo_focus_mins = 25
        self.pomo_break_mins = 5
        self.pomo_remaining  = 25 * 60
        self.pomo_total      = 25 * 60
        self.pomo_label      = store.data.get('_pomo_label', '')

        self.todo_scroll    = 0
        self.habit_scroll   = 0
        self._scroll_todo   = 0.0
        self._scroll_habit  = 0.0
        self._todo_scroll_f = 0.0

        self.stats_range      = '7d'
        self.stats_scroll     = 0
        self._press_time      = 0
        self._long_press_id   = None
        self._stats_content_h = 9999
        self._stats_cache     = {}
        self._stats_cache_key = None
        self._stats_last_date = None

        self._tab_rects        = []
        self._btn_rects        = []
        self._preset_rects     = []
        self._pomo_adj_rects   = []
        self._goal_rects       = []
        self.pomo_daily_goal   = 4
        self._pomo_label_rect  = None
        self._todo_item_rects  = []
        self._todo_rects       = []
        self._todo_done_rects  = []
        self._add_todo_rect    = None
        self._clear_done_rect  = None
        self._habit_rects      = []
        self._habit_add_rect   = None
        self._stats_range_rects = []
        self._replenish_rect   = None

        self._streak_cache = {}

        try:
            self._settings_vol = self._load_settings_file().get('volume', 80)
        except Exception:
            self._settings_vol = 80
        self._settings_rects = {}
        self._vol_dragging    = False
        self._habit_hwm       = {}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(PANEL_W, PANEL_H)

    @property
    def window_rect(self):
        if not self.open:
            return None
        try:
            p = self.pos()
            return (p.x(), p.y(), PANEL_W, PANEL_H)
        except Exception:
            return None

    def show(self, cat_wx: int, cat_wy: int, cat_w: int, cat_h: int,
             screen_w: int, screen_h: int) -> None:
        self.open = True
        today_str = str(date.today())
        if self._stats_last_date != today_str:
            self._stats_cache_key = None
            self._stats_last_date = today_str

        px = cat_wx - PANEL_W - 4
        py = cat_wy - PANEL_H + cat_h
        if px < 0:
            px = cat_wx + cat_w + 4
        py = max(0, min(py, screen_h - PANEL_H - 10))

        self.move(int(px), int(py))
        super().show()
        self.raise_()
        self.activateWindow()
        self.update()
        try:
            self._app.raise_()
        except Exception:
            pass

    def hide(self) -> None:
        self.open = False
        super().hide()

    def invalidate_stats(self) -> None:
        self._stats_cache_key = None

    def invalidate_streaks(self) -> None:
        self._streak_cache = {}
        self._habit_hwm    = {}

    def queue_draw(self) -> None:
        self.update()

    def destroy(self) -> None:
        self.close()

    def pomo_tick(self) -> None:
        if not self.pomo_running:
            return
        self.pomo_remaining -= 1
        if self.pomo_remaining <= 0:
            self._app.handle_pomo_end()
        elif self.open:
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        try:
            self._draw_panel(p)
        except Exception as e:
            import traceback
            print(f'[panel] draw error: {e}')
            traceback.print_exc()
        p.end()

    def focusOutEvent(self, event) -> None:
        if self.dialog_open:
            return

        def _close():
            from PySide6.QtWidgets import QApplication
            active = QApplication.activeWindow()
            if active in (self, self._app):
                return False
            self.hide()
            self._app.da.queue_draw()
            return False

        GLib.timeout_add(150, _close)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            self._app.da.queue_draw()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._app.record_interaction()
        ex, ey = event.position().x(), event.position().y()
        self._press_time = time.time() * 1000

        if self.active_tab == 'habits':
            for bx, by, bw, bh, hid, action, period in self._habit_rects:
                if action == 'detail' and bx <= ex <= bx + bw and by <= ey <= by + bh:
                    self._show_habit_detail(hid)
                    return

            for bx, by, bw, bh, hid, action, period in self._habit_rects:
                if action == '_row' and bx <= ex <= bx + bw and by <= ey <= by + bh:
                    hab = next((h for h in self._store.habits.get('habits', []) if h['id'] == hid), None)
                    if hab and hab.get('type', 'counter') == 'counter':
                        mid_start = bx + bw * 0.3
                        mid_end   = bx + bw * 0.7
                        if mid_start <= ex <= mid_end:
                            def _check_long(hid2=hid, period2=period, hab2=hab):
                                if self.active_tab == 'habits':
                                    self._show_set_value_dialog(hid2, period2, hab2)
                                return False
                            self._long_press_id = GLib.timeout_add(600, _check_long)
                    break

        for tx, ty, tw, th, tid in self._tab_rects:
            if tx <= ex <= tx + tw and ty <= ey <= ty + th:
                self.active_tab   = tid
                self.stats_scroll = 0
                self.update()
                return

        if self.active_tab == 'stats':
            rr = self._replenish_rect
            if rr:
                bx, by, bw, bh = rr
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    if self._app.economy.replenish_heart():
                        self._bubble.say('One life restored!', 3)
                        self._app.effects.spawn('heal', 1.0, *self._app.get_win_pos())
                    else:
                        self._bubble.say('Not enough coins...', 3)
                    self.update()
                    return
            for bx, by, bw, bh, rid in self._stats_range_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    self.stats_range      = rid
                    self._stats_cache_key = None
                    self.stats_scroll     = 0
                    self.update()
                    return

        elif self.active_tab == 'habits':
            self._handle_habit_click(ex, ey)
        elif self.active_tab == 'settings':
            self._handle_settings_click(ex, ey)

        elif self.active_tab == 'pomo':
            lr = self._pomo_label_rect
            if lr and lr[0] <= ex <= lr[0] + lr[2] and lr[1] <= ey <= lr[1] + lr[3]:
                self._edit_pomo_label(); return

            for bx, by, bw, bh, action in self._pomo_adj_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    if not self.pomo_running:
                        if action   == 'focus_inc': self.pomo_focus_mins = min(90,  self.pomo_focus_mins + 1)
                        elif action == 'focus_dec': self.pomo_focus_mins = max(1,   self.pomo_focus_mins - 1)
                        elif action == 'break_inc': self.pomo_break_mins = min(30,  self.pomo_break_mins + 1)
                        elif action == 'break_dec': self.pomo_break_mins = max(1,   self.pomo_break_mins - 1)
                        self.pomo_remaining = self.pomo_focus_mins * 60
                        self.pomo_total     = self.pomo_focus_mins * 60
                        self.pomo_is_break  = False
                    self.update(); return

            for bx, by, bw, bh, f, b in self._preset_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    if not self.pomo_running:
                        self.pomo_focus_mins = f; self.pomo_break_mins = b
                        self.pomo_remaining  = f * 60; self.pomo_total  = f * 60
                        self.pomo_is_break   = False
                    self.update(); return

            for bx, by, bw, bh, action in self._goal_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    if action == 'goal_inc':
                        self.pomo_daily_goal = min(12, self.pomo_daily_goal + 1)
                    elif action == 'goal_dec':
                        self.pomo_daily_goal = max(1, self.pomo_daily_goal - 1)
                    self.update(); return

            for bx, by, bw, bh, action in self._btn_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    if action == 'toggle':
                        self.pomo_running = not self.pomo_running
                        if self.pomo_running:
                            self._app.anim.set_state('walk')
                            lines = ["Focus mode on.", "Let's go!", "Time to grind~",
                                     "You got this!", "No distractions!", "Heads down!"]
                            if self._store.bond >= 70:
                                lines += ["I believe in you!", "We got this!"]
                            self._bubble.think(random.choice(lines), 3)
                        else:
                            self._app.anim.set_state('idle')
                            self._bubble.say(random.choice(["Paused.", "Break?", "Rest a sec~"]), 3)
                    elif action == 'reset':
                        self.pomo_running   = False
                        self.pomo_is_break  = False
                        self.pomo_remaining = self.pomo_focus_mins * 60
                        self.pomo_total     = self.pomo_focus_mins * 60
                        self._app.anim.set_state('idle')
                        self._bubble.say("Ready.", 2)
                    self.update()
                    self._app.da.queue_draw()
                    return

        else:
            for fx, fy, fw, fh, ftag in self._tag_filter_rects:
                if fx <= ex <= fx + fw and fy <= ey <= fy + fh:
                    self.active_tag_filter = ftag
                    self.todo_scroll = 0
                    self.update()
                    return

            ar = self._add_todo_rect
            if ar and ar[0] <= ex <= ar[0] + ar[2] and ar[1] <= ey <= ar[1] + ar[3]:
                self._show_add_todo_dialog(); return

            cdr = self._clear_done_rect
            if cdr and cdr[0] <= ex <= cdr[0] + cdr[2] and cdr[1] <= ey <= cdr[1] + cdr[3]:
                self._store.data['todos'] = [t for t in self._store.data.get('todos', []) if not t.get('done')]
                self._store.flush_data()
                self._bubble.say("Cleared!", 2)
                self.update(); return

            for bx, by, bw, bh, tid in self._todo_done_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    for t in self._store.data['todos']:
                        if t['id'] == tid:
                            t['done'] = not t.get('done', False)
                            if t['done']:
                                self._store.record_todo_done()
                                self.invalidate_stats()
                                streak = self._store.current_streak()
                                coins  = self._app.economy.earn_todo(t.get('priority', 'med'), streak)
                                self._app.effects.spawn('coins', coins, *self._app.get_win_pos())
                                from utils import _play_sound
                                from config import SUCCESS_FILE
                                _play_sound(SUCCESS_FILE, 'success')
                                recur = t.get('recur', '')
                                if recur:
                                    new_t = _spawn_recur_task(t)
                                    self._store.data['todos'].insert(0, new_t)
                                    self._bubble.say("Done! [+] scheduled", 2)
                                else:
                                    self._bubble.say("Task done!", 2)
                                self._app.anim.play_transition('lick', 'idle')
                            break
                    self._store.flush_data()
                    self.update()
                    self._app.da.queue_draw()
                    return

            for bx, by, bw, bh, tid in self._todo_rects:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    todo = next((t for t in self._store.data['todos'] if t['id'] == tid), None)
                    if todo:
                        self._show_edit_todo_dialog(todo)
                    return

    def mouseReleaseEvent(self, event) -> None:
        if self._vol_dragging:
            self._vol_dragging = False
            s = self._load_settings_file()
            s['volume'] = self._settings_vol
            self._save_settings_file(s)

    def mouseMoveEvent(self, event) -> None:
        if not self._vol_dragging:
            return
        if self.active_tab != 'settings':
            return
        vr = self._settings_rects.get('vol_slider')
        if not vr:
            return
        bx, by, bw, bh = vr
        ex = event.position().x()
        self._settings_vol = int(max(0, min(100, (ex - bx) / bw * 100)))
        import os
        os.environ['BUDDY_VOLUME'] = str(self._settings_vol)
        self.update()

    def wheelEvent(self, event) -> None:
        delta_y = event.angleDelta().y() / 120.0

        if self.active_tab == 'todo':
            todos      = self._store.data.get('todos', [])
            max_offset = max(0, len(todos) - 6)
            self._scroll_todo_acc = getattr(self, '_scroll_todo_acc', 0.0) + delta_y
            steps = int(self._scroll_todo_acc)
            if steps:
                self._scroll_todo_acc -= steps
                self._scroll_todo = int(max(0, min(self._scroll_todo - steps, max_offset)))
                self.update()

        elif self.active_tab == 'habits':
            habits      = self._store.habits.get('habits', [])
            max_visible = max(1, (PANEL_H - 96) // 130)
            max_offset  = max(0, len(habits) - max_visible)
            self._scroll_habit_acc = getattr(self, '_scroll_habit_acc', 0.0) + delta_y
            steps = int(self._scroll_habit_acc)
            if steps:
                self._scroll_habit_acc -= steps
                self.habit_scroll = int(max(0, min(self.habit_scroll - steps, max_offset)))
                self.update()

        elif self.active_tab == 'stats':
            content_h  = self._stats_content_h
            visible_h  = PANEL_H - 48
            max_scroll = max(0, content_h - visible_h)
            STATS_SCROLL_STEP = 20
            self.stats_scroll = max(0, min(self.stats_scroll - int(delta_y * STATS_SCROLL_STEP), max_scroll))
            self.update()

    def _draw_panel(self, p: QPainter) -> None:
        px, py = 0, 0
        pw, ph = PANEL_W, PANEL_H

        _fill_rrect(p, px, py, pw, ph, 16, _qc(BASE, 0.97))
        _stroke_rrect(p, px, py, pw, ph, 16, _qc(MAUVE, 0.25), 1.5)

        _fill_rrect(p, px, py, pw, 28, 16, _qc(MAUVE, 0.06))

        bond = self._store.bond
        if bond >= 75:   bcolor = (0.40, 0.85, 0.45)
        elif bond >= 50: bcolor = (0.97, 0.88, 0.30)
        elif bond >= 25: bcolor = (1.00, 0.55, 0.15)
        else:            bcolor = (0.95, 0.25, 0.30)
        bw, bh = 28, 5
        bx3, by3 = px + 6, py + 11
        _fill_rrect(p, bx3, by3, bw, bh, 2, _qc(OVERLAY, 0.5))
        _fill_rrect(p, bx3, by3, max(2, int(bw * bond / 100)), bh, 2, _qc(bcolor, 0.85))

        tab_y, tab_h = py + 8, 26
        self._tab_rects = []
        tabs = [('Pomo', 'pomo'), ('Todo', 'todo'), ('Habits', 'habits'), ('Stats', 'stats'), ('Cfg', 'settings')]
        for i, (label, tid) in enumerate(tabs):
            tw  = (pw - 20) // 5
            tx  = px + 6 + i * (tw + 3)
            act = self.active_tab == tid
            if act:
                _fill_rrect(p, tx, tab_y, tw, tab_h, 8, _qc(MAUVE, 0.22))
                pen = QPen(_qc(MAUVE)); pen.setWidthF(2)
                p.setPen(pen)
                p.drawLine(QPointF(tx + 8, tab_y + tab_h - 1), QPointF(tx + tw - 8, tab_y + tab_h - 1))
                txt_col = _qc(MAUVE)
            else:
                txt_col = _qc(SUBTEXT)
            _set_font(p, 9, bold=act)
            tlw = _text_w(p, label)
            _draw_text(p, tx + (tw - tlw) // 2, tab_y + 17, label, txt_col, 9, bold=act)
            self._tab_rects.append((tx, tab_y, tw, tab_h, tid))

        sep_y     = tab_y + tab_h + 3
        _divider(p, px, sep_y, pw)
        content_y = sep_y + 6

        if   self.active_tab == 'pomo':     self._draw_pomo(p, px, content_y, pw)
        elif self.active_tab == 'todo':     self._draw_todos(p, px, content_y, pw)
        elif self.active_tab == 'habits':   self._draw_habits(p, px, content_y, pw)
        elif self.active_tab == 'settings':
            p.save()
            p.setClipRect(QRectF(px, content_y, pw, PANEL_H - content_y))
            self._draw_settings(p, px, content_y, pw)
            p.restore()
        else:
            p.save()
            p.setClipRect(QRectF(px, content_y, pw, PANEL_H - content_y))
            p.translate(0, -self.stats_scroll)
            self._draw_stats(p, px, content_y, pw)
            p.restore()
            if self._stats_content_h > PANEL_H - content_y:
                arrow = 'v' if self.stats_scroll < self._stats_content_h - (PANEL_H - content_y) else '^'
                _draw_text(p, px + pw // 2 - 3, PANEL_H - 6, arrow, _qc(SUBTEXT, 0.45), 9, bold=True)

    def _draw_pomo(self, p: QPainter, px, y, pw) -> None:
        cx = px + pw // 2

        _draw_text(p, px + 10, y + 18, 'Pomodoro', _qc(TEXT), 15, bold=True)

        today_data = self._store.stats.get('daily', {}).get(str(date.today()), {})
        t_sessions = today_data.get('sessions', 0)
        t_mins     = today_data.get('focus_mins', 0)
        _draw_text(p, px + 10, y + 32, f'{t_sessions} sessions  -  {t_mins}m focused today',
                   _qc(SUBTEXT, 0.8), 9)
        y += 44

        card_h = 148
        _fill_rrect(p, px + 6, y, pw - 12, card_h, 12, QColor(33, 31, 46, 247))

        ring_color = GREEN if self.pomo_is_break else MAUVE
        R  = 44
        ry = y + R + 14

        _stroke_circle(p, cx, ry, R, _qc(ring_color, 0.08), 10)
        _stroke_circle(p, cx, ry, R, _qc(OVERLAY, 0.5), 8)

        pct = (self.pomo_total - self.pomo_remaining) / max(self.pomo_total, 1)
        if pct > 0:
            path = _arc_path(cx, ry, R, -math.pi / 2, -math.pi / 2 + pct * 2 * math.pi)
            pen = QPen(_qc(ring_color)); pen.setWidthF(8); pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        m, s     = divmod(self.pomo_remaining, 60)
        time_str = f'{m:02d}:{s:02d}'
        _set_font(p, 20, bold=True)
        tw_ = _text_w(p, time_str)
        _draw_text(p, cx - tw_ / 2, ry + 9, time_str, _qc(TEXT), 20, bold=True)

        is_paused = not self.pomo_running and 0 < self.pomo_remaining < self.pomo_total
        sub_text  = '|| PAUSED' if is_paused else ('Break' if self.pomo_is_break else 'Focus')
        sub_col   = YELLOW if is_paused else ring_color
        _set_font(p, 8, bold=True)
        sw_ = _text_w(p, sub_text)
        pill_w = sw_ + 16
        pill_x = cx - pill_w / 2
        pill_y = ry + R + 10
        _fill_rrect(p, pill_x, pill_y, pill_w, 16, 8, _qc(sub_col, 0.18))
        _stroke_rrect(p, pill_x, pill_y, pill_w, 16, 8, _qc(sub_col, 0.6), 1)
        _draw_text(p, cx - sw_ / 2, pill_y + 11, sub_text, _qc(sub_col), 8, bold=True)

        y += card_h + 4

        _fill_rrect(p, px + 6, y, pw - 12, 30, 10, QColor(33, 31, 46, 247))
        _draw_text(p, px + 16, y + 11, 'working on', _qc(SUBTEXT, 0.5), 8)
        field_x = px + 76; field_w = pw - 94
        display   = self.pomo_label or ''
        truncated = display[:22] + '...' if len(display) > 23 else display
        _draw_text(p, field_x, y + 19, truncated or 'tap to set...',
                   _qc(TEXT if display else SUBTEXT, 0.9 if display else 0.35), 9, bold=bool(display))
        self._pomo_label_rect = (px + 10, y, pw - 20, 30)
        y += 36

        _fill_rrect(p, px + 6, y, pw - 12, 42, 10, QColor(33, 31, 46, 247))
        _draw_text(p, px + 16, y + 11, 'PRESETS', _qc(SUBTEXT, 0.5), 8, bold=True)

        presets = [('25/5', 25, 5), ('50/10', 50, 10), ('15/3', 15, 3)]
        pw3     = (pw - 32) // 3
        self._preset_rects = []
        for i, (label, f, b) in enumerate(presets):
            bx     = px + 10 + i * (pw3 + 4)
            active = self.pomo_focus_mins == f and self.pomo_break_mins == b
            _fill_rrect(p, bx, y + 14, pw3, 20, 6, _qc(SKY, 0.25 if active else 0.06))
            _stroke_rrect(p, bx, y + 14, pw3, 20, 6, _qc(SKY, 0.8 if active else 0.2), 1 if active else 0.5)
            _set_font(p, 8.5, bold=active)
            lw = _text_w(p, label)
            _draw_text(p, bx + (pw3 - lw) / 2, y + 28, label, _qc(SKY if active else SUBTEXT), 8.5, bold=active)
            self._preset_rects.append((bx, y + 14, pw3, 20, f, b))
        y += 46

        _fill_rrect(p, px + 6, y, pw - 12, 38, 10, QColor(33, 31, 46, 247))
        self._pomo_adj_rects = []

        def _adj_col(label, val, col_x):
            _draw_text(p, col_x, y + 14, label, _qc(SUBTEXT, 0.6), 8)
            btn_sz = 18
            mx1 = col_x + 40
            _fill_circle(p, mx1 + btn_sz // 2, y + 28, btn_sz // 2, _qc(OVERLAY, 0.5))
            pen = QPen(_qc(SUBTEXT)); pen.setWidthF(1.5); p.setPen(pen)
            p.drawLine(QPointF(mx1 + 4, y + 28), QPointF(mx1 + 14, y + 28))
            self._pomo_adj_rects.append((mx1, y + 19, btn_sz, btn_sz, f'{label}_dec'))

            vstr = str(val)
            _set_font(p, 10, bold=True)
            vw = _text_w(p, vstr)
            _draw_text(p, mx1 + btn_sz + (20 - vw) // 2, y + 33, vstr, _qc(TEXT), 10, bold=True)

            mx2 = mx1 + btn_sz + 20
            _fill_circle(p, mx2 + btn_sz // 2, y + 28, btn_sz // 2, _qc(OVERLAY, 0.5))
            p.setPen(pen)
            p.drawLine(QPointF(mx2 + 4, y + 28), QPointF(mx2 + 14, y + 28))
            p.drawLine(QPointF(mx2 + 9, y + 23), QPointF(mx2 + 9, y + 33))
            self._pomo_adj_rects.append((mx2, y + 19, btn_sz, btn_sz, f'{label}_inc'))

        _adj_col('focus', self.pomo_focus_mins, px + 14)
        _adj_col('break', self.pomo_break_mins, px + pw // 2 + 4)
        y += 44

        goal_h = 38
        _fill_rrect(p, px + 6, y, pw - 12, goal_h, 10, QColor(33, 31, 46, 247))

        goal_sessions = getattr(self, 'pomo_daily_goal', 4)
        done_sessions = t_sessions
        _draw_text(p, px + 14, y + 13, 'DAILY GOAL', _qc(SUBTEXT, 0.5), 8, bold=True)

        dot_r, dot_gap = 5, 14
        dot_start = px + 14
        for gi in range(min(goal_sessions, 8)):
            dx = dot_start + gi * dot_gap
            dy = y + 26
            _fill_circle(p, dx + dot_r, dy, dot_r, _qc(GREEN, 0.85) if gi < done_sessions else _qc(OVERLAY, 0.5))

        gbx = px + pw - 60
        self._goal_rects = []
        for gi2, (sym, act) in enumerate([('+', 'goal_inc'), ('-', 'goal_dec')]):
            gx = gbx + gi2 * 26
            _fill_rrect(p, gx, y + 18, 20, 14, 4, _qc(OVERLAY, 0.4))
            _set_font(p, 10, bold=True)
            _draw_text(p, gx + 5, y + 29, sym, _qc(SUBTEXT, 0.7), 10, bold=True)
            self._goal_rects.append((gx, y + 18, 20, 14, act))

        gval_str = f'{done_sessions}/{goal_sessions}'
        _set_font(p, 9, bold=True)
        gw = _text_w(p, gval_str)
        col_g = GREEN if done_sessions >= goal_sessions else MAUVE
        _draw_text(p, gbx - gw - 6, y + 29, gval_str, _qc(col_g, 0.9), 9, bold=True)
        y += goal_h + 4

        btn_h = 32
        btn_w = (pw - 28) // 2
        self._btn_rects = []
        is_paused_mid = not self.pomo_running and 0 < self.pomo_remaining < self.pomo_total
        start_label   = 'Resume' if is_paused_mid else ('Pause' if self.pomo_running else 'Start')
        btn_color_s   = MAUVE if not self.pomo_running else RED

        for i, (label, color, action) in enumerate([
            (start_label, btn_color_s, 'toggle'),
            ('Reset',     OVERLAY,     'reset'),
        ]):
            bx = px + 8 + i * (btn_w + 8)
            _fill_rrect(p, bx, y, btn_w, btn_h, 10, _qc(color, 0.9 if i == 0 else 0.4))
            _set_font(p, 11, bold=True)
            lw = _text_w(p, label)
            _draw_text(p, bx + (btn_w - lw) / 2, y + 20, label, _qc(BASE if i == 0 else TEXT), 11, bold=True)
            self._btn_rects.append((bx, y, btn_w, btn_h, action))

        y += btn_h + 4

        sessions = [s for s in self._store.stats.get('sessions', [])
                    if s.get('date') == str(date.today())]
        if sessions:
            box_h = min(len(sessions), 4) * 18 + 16
            _fill_rrect(p, px + 6, y, pw - 12, box_h, 10, QColor(33, 31, 46, 247))
            _draw_text(p, px + 14, y + 12, "TODAY'S SESSIONS", _qc(SUBTEXT, 0.5), 8, bold=True)
            sy = y + 20
            for si, sess in enumerate(reversed(sessions[-4:])):
                t_str = sess.get('time', '')
                m_str = f"{sess.get('mins', 0)}m"
                lbl   = sess.get('label', '')
                _draw_text(p, px + 14, sy + 10, t_str, _qc(SUBTEXT, 0.5), 8)
                _draw_text(p, px + 50, sy + 10, m_str, _qc(GREEN, 0.8), 8)
                if lbl:
                    _draw_text(p, px + 72, sy + 10, lbl[:22], _qc(TEXT, 0.6), 8)
                sy += 18

    def _draw_todos(self, p: QPainter, px, y, pw) -> None:
        todos = self._store.data.get('todos', [])

        all_tags = []
        for t in todos:
            for tag in t.get('tags', []):
                if tag not in all_tags:
                    all_tags.append(tag)

        def sort_key(t):
            if t.get('done'): return (3, 0, '')
            d    = _days_until(t.get('due', ''))
            tier = 0 if (d is not None and d < 0) else (1 if (d is not None and d <= 3) else 2)
            pr   = {'high': 0, 'med': 1, 'low': 2}.get(t.get('priority', 'med'), 1)
            return (tier, pr, t.get('due', '9999-99-99'))

        filtered     = [t for t in todos if not self.active_tag_filter or self.active_tag_filter in t.get('tags', [])]
        todos_sorted = sorted(filtered, key=sort_key)
        pending      = [t for t in todos_sorted if not t.get('done')]
        done_list    = [t for t in todos_sorted if t.get('done')]

        _draw_text(p, px + 10, y + 18, 'Tasks', _qc(TEXT), 15, bold=True)

        done_count = len(done_list)
        sub = f'{done_count} completed'
        if self.active_tag_filter: sub += f'  #{self.active_tag_filter}'
        _draw_text(p, px + 10, y + 32, sub, _qc(SUBTEXT, 0.8), 9)

        abx, aby = px + pw - 34, y + 4
        abw, abh = 26, 26
        _fill_circle(p, abx + abw // 2, aby + abh // 2, abw // 2, _qc(MAUVE, 0.2))
        _stroke_circle(p, abx + abw // 2, aby + abh // 2, abw // 2, _qc(MAUVE, 0.6), 1)
        _draw_text(p, abx + 7, aby + 19, '+', _qc(MAUVE), 18, bold=True)
        self._add_todo_rect = (abx, aby, abw, abh)

        self._clear_done_rect = None
        if done_list:
            cdx, cdy = px + pw - 96, y + 7
            cdw, cdh = 56, 20
            _fill_rrect(p, cdx, cdy, cdw, cdh, 6, _qc(OVERLAY, 0.4))
            _stroke_rrect(p, cdx, cdy, cdw, cdh, 6, _qc(SUBTEXT, 0.4), 1)
            _draw_text(p, cdx + 8, cdy + 13, 'clear done', _qc(SUBTEXT, 0.8), 7.5)
            self._clear_done_rect = (cdx, cdy, cdw, cdh)

        y += 40

        self._tag_filter_rects = []
        if all_tags:
            tag_x = px + 8
            all_active = not self.active_tag_filter
            _fill_rrect(p, tag_x, y, 26, 16, 8, _qc(MAUVE, 0.8 if all_active else 0.15))
            _set_font(p, 7.5, bold=True)
            _draw_text(p, tag_x + 5, y + 11, 'All', _qc(BASE if all_active else SUBTEXT), 7.5, bold=True)
            self._tag_filter_rects.append((tag_x, y, 26, 16, ''))
            tag_x += 32

            for tag in all_tags[:5]:
                active = (self.active_tag_filter == tag)
                tc     = _tag_color(tag)
                _set_font(p, 7.5, bold=True)
                tw = _text_w(p, f'#{tag}') + 12
                _fill_rrect(p, tag_x, y, tw, 16, 8, _qc(tc, 0.8 if active else 0.2))
                _draw_text(p, tag_x + 6, y + 11, f'#{tag}', _qc(BASE if active else tc), 7.5, bold=True)
                self._tag_filter_rects.append((tag_x, y, tw, 16, tag))
                tag_x += tw + 6
                if tag_x > px + pw - 20: break
            y += 24

        self._todo_rects      = []
        self._todo_done_rects = []

        CARD_H_BASE = 44
        all_display = pending + (done_list if done_list else [])
        if not all_display:
            self._draw_empty_state(p, px, pw, y, 'Nothing to do!', 'tap + to add a task')
            return

        SCROLL_UNIT = CARD_H_BASE + 6
        max_items    = max(1, (PANEL_H - y - 4) // SCROLL_UNIT)
        self._scroll_todo = int(max(0, min(self._scroll_todo, max(0, len(all_display) - max_items))))
        visible = all_display[int(self._scroll_todo):int(self._scroll_todo) + max_items + 2]

        for t in visible:
            done     = t.get('done', False)
            text     = _strip_tags(t.get('text', ''))
            due      = t.get('due', '')
            priority = t.get('priority', 'med')
            tags     = t.get('tags', [])
            tid      = t.get('id')
            d        = _days_until(due)
            overdue  = d is not None and d < 0 and not done

            ix, iw = px + 6, pw - 12

            _set_font(p, 10)
            max_text_w = iw - 40
            words = text.split()
            lines, line = [], ''
            for w in words:
                test = (line + ' ' + w).strip()
                if _text_w(p, test) > max_text_w and line:
                    lines.append(line); line = w
                else: line = test
            if line: lines.append(line)
            n_lines = max(1, len(lines))
            ih = max(CARD_H_BASE, 14 + n_lines * 14 + (16 if due else 0) + (14 if tags else 0) + 10)

            if y + ih > PANEL_H - 4:
                break

            if done:
                _fill_rrect(p, ix, y, iw, ih, 10, QColor(33, 31, 46, 128))
            elif overdue:
                pulse = (math.sin(time.time() * 2.5) + 1) / 2
                _fill_rrect(p, ix, y, iw, ih, 10, QColor(int((0.30 + pulse * 0.08) * 255), 20, 26, 242))
            else:
                _fill_rrect(p, ix, y, iw, ih, 10, QColor(33, 31, 46, 247))

            prio_col = {'high': RED, 'med': SKY, 'low': MAUVE}.get(priority, SKY)
            if not done:
                p.fillRect(QRectF(ix, y + 8, 3, ih - 16), _qc(prio_col, 0.7))

            ccx, ccy = ix + 16, y + ih // 2
            if done:
                _fill_circle(p, ccx, ccy, 7, _qc(GREEN, 0.3))
                _stroke_circle(p, ccx, ccy, 7, _qc(GREEN, 0.8), 1.5)
                pen = QPen(_qc(GREEN)); pen.setWidthF(1.8); p.setPen(pen)
                p.drawLine(QPointF(ccx - 3, ccy), QPointF(ccx - 1, ccy + 3))
                p.drawLine(QPointF(ccx - 1, ccy + 3), QPointF(ccx + 4, ccy - 3))
            else:
                _fill_circle(p, ccx, ccy, 7, _qc(OVERLAY, 0.4))
                _stroke_circle(p, ccx, ccy, 7, _qc(prio_col, 0.6), 1.5)
            self._todo_done_rects.append((ccx - 8, ccy - 8, 16, 16, tid))

            text_x = ix + 30
            text_y = y + 14
            _set_font(p, 10, bold=not done)
            text_col = _qc(SUBTEXT if done else TEXT, 0.5 if done else 1.0)
            for li, ln in enumerate(lines):
                _draw_text(p, text_x, text_y + li * 14, ln, text_col, 10, bold=not done)
                if done:
                    lw = _text_w(p, ln)
                    pen = QPen(text_col); pen.setWidthF(1.2); p.setPen(pen)
                    p.drawLine(QPointF(text_x, text_y + li * 14 - 4), QPointF(text_x + lw, text_y + li * 14 - 4))

            row2_y = text_y + n_lines * 14

            if due and not done:
                _set_font(p, 8)
                if overdue:
                    hrs = abs(d) * 24
                    due_lbl = f'{int(hrs)}h overdue' if hrs < 24 else f'{int(abs(d))}d overdue'
                    tc = RED
                elif d * 24 < 24:
                    hrs = int(d * 24); mins = int((d * 24 - hrs) * 60)
                    due_lbl = f'{hrs}h {mins}m left' if hrs > 0 else f'{mins}m left'
                    tc = YELLOW
                elif d <= 3:
                    due_lbl = f'{int(d)}d left'; tc = YELLOW
                else:
                    if 'T' in due:
                        dt2 = datetime.fromisoformat(due)
                        due_lbl = dt2.strftime('%d %b @ %I:%M %p')
                    else:
                        due_lbl = due[5:]
                    tc = SUBTEXT
                _fill_circle(p, text_x + 4, row2_y + 4, 4, _qc(tc, 0.4))
                _draw_text(p, text_x + 12, row2_y + 8, due_lbl, _qc(tc, 0.9), 8)
                row2_y += 14

            if tags and not done:
                tx2 = text_x
                for tag in tags[:3]:
                    tc2 = _tag_color(tag)
                    _set_font(p, 7, bold=True)
                    tw2 = _text_w(p, f'#{tag}') + 8
                    _fill_rrect(p, tx2, row2_y, tw2, 12, 4, _qc(tc2, 0.2))
                    _draw_text(p, tx2 + 4, row2_y + 9, f'#{tag}', _qc(tc2, 0.9), 7, bold=True)
                    tx2 += tw2 + 4
                    if tx2 > ix + iw - 20: break

            ebx = ix + iw - 22
            eby = y + ih // 2 - 8
            _fill_circle(p, ebx + 8, eby + 8, 8, _qc(SUBTEXT, 0.2))
            _draw_text(p, ebx + 3, eby + 12, '...', _qc(SUBTEXT, 0.6), 9)
            self._todo_rects.append((ebx, eby, 16, 16, tid))

            y += ih + 6

        if self._scroll_todo > 0 or (self._scroll_todo + max_items < len(all_display)):
            if self._scroll_todo + max_items < len(all_display):
                _draw_text(p, px + pw // 2 - 5, PANEL_H - 8, 'v', _qc(SUBTEXT, 0.35), 8, bold=True)

    def _draw_empty_state(self, p: QPainter, px, pw, y, msg: str, sub: str) -> None:
        cx3, cy3 = px + pw // 2, y + 55
        _fill_circle(p, cx3, cy3, 20, _qc(OVERLAY, 0.25))
        _fill_circle(p, cx3, cy3 - 24, 13, _qc(OVERLAY, 0.25))
        for ex3, ey3 in [(cx3 - 9, cy3 - 35), (cx3 + 9, cy3 - 35)]:
            path = QPainterPath()
            path.moveTo(ex3, ey3); path.lineTo(ex3 - 5, ey3 - 9); path.lineTo(ex3 + 5, ey3 - 9)
            path.closeSubpath()
            p.setPen(Qt.NoPen); p.setBrush(_qc(MAUVE, 0.25)); p.drawPath(path)
        for ex3 in [cx3 - 4, cx3 + 4]:
            _fill_circle(p, ex3, cy3 - 26, 1.5, _qc(SUBTEXT, 0.4))
        _set_font(p, 10, bold=True)
        mw = _text_w(p, msg)
        _draw_text(p, px + pw // 2 - mw / 2, y + 94, msg, _qc(SUBTEXT, 0.5), 10, bold=True)
        _set_font(p, 8)
        sw = _text_w(p, sub)
        _draw_text(p, px + pw // 2 - sw / 2, y + 110, sub, _qc(SUBTEXT, 0.35), 8)

    def _draw_habits(self, p: QPainter, px, y, pw) -> None:
        hd     = self._store.habits
        habits = hd.get('habits', [])
        log    = hd.get('log', {})
        today  = date.today()

        _draw_text(p, px + 10, y + 18, 'Habits', _qc(TEXT), 15, bold=True)

        if habits:
            done_today = sum(
                1 for h in habits
                if (h.get('type') == 'checkbox' and log.get(h['id'], {}).get(_get_period_key(h.get('mode', 'daily')), 0))
                or (h.get('type', 'counter') != 'checkbox' and h.get('goal', 0) > 0
                    and log.get(h['id'], {}).get(_get_period_key(h.get('mode', 'daily')), 0) >= h.get('goal', 0))
            )
            total = len(habits)
            _draw_text(p, px + 10, y + 32, f'{done_today}/{total} Completed Today', _qc(SUBTEXT, 0.8), 9)

        abx, aby = px + pw - 32, y + 4
        abw, abh = 24, 24
        _fill_circle(p, abx + abw // 2, aby + abh // 2, abw // 2, _qc(MAUVE, 0.2))
        _stroke_circle(p, abx + abw // 2, aby + abh // 2, abw // 2, _qc(MAUVE, 0.6), 1)
        _draw_text(p, abx + 6, aby + 17, '+', _qc(MAUVE), 16, bold=True)
        self._habit_add_rect = (abx, aby, abw, abh)

        y += 40
        self._habit_rects = []

        if not habits:
            self._draw_empty_state(p, px, pw, y, 'No habits yet!', 'tap + to build a habit')
            return

        CARD_H = 122
        available_h  = PANEL_H - y - 4
        max_visible  = max(1, available_h // CARD_H)
        self.habit_scroll = int(max(0, min(self.habit_scroll, len(habits) - max_visible)))
        visible_habits = habits[self.habit_scroll:self.habit_scroll + max_visible]

        if self.habit_scroll > 0:
            _draw_text(p, px + pw // 2 - 3, y - 4, '^', _qc(SUBTEXT, 0.5), 9, bold=True)
        if self.habit_scroll + max_visible < len(habits):
            _draw_text(p, px + pw // 2 - 3, y + max_visible * CARD_H + 2, 'v', _qc(SUBTEXT, 0.5), 9, bold=True)

        for h in visible_habits:
            htype  = h.get('type', 'counter')
            mode   = h.get('mode', 'daily')
            period = _get_period_key(mode)
            hid    = h['id']
            raw    = log.get(hid, {}).get(period, 0)
            done   = bool(raw) if htype == 'checkbox' else False
            val    = raw
            goal   = h.get('goal', 0)
            name   = h.get('name', 'Habit')
            unit   = h.get('unit', '')
            streak_count = self._calc_streak(hid, mode, log)
            pct_done = min(1.0, val / goal) if htype == 'counter' and goal > 0 else (1.0 if done else 0.0)
            is_complete = pct_done >= 1.0

            ix, iw = px + 6, pw - 12
            ih = CARD_H

            _fill_rrect(p, ix, y, iw, ih, 12, QColor(33, 31, 46, 247))

            accent = GREEN if is_complete else MAUVE
            p.fillRect(QRectF(ix + 12, y, iw - 24, 2), _qc(accent, 0.6 if is_complete else 0.3))

            row1_y = y + 34

            cx2, cy2, cr2 = ix + 18, row1_y, 11
            if is_complete:
                _fill_circle(p, cx2, cy2, cr2, _qc(GREEN, 0.25))
                _stroke_circle(p, cx2, cy2, cr2, _qc(GREEN, 0.9), 1.5)
                pen = QPen(_qc(GREEN)); pen.setWidthF(1.8); p.setPen(pen)
                p.drawLine(QPointF(cx2 - 4, cy2), QPointF(cx2 - 1, cy2 + 3))
                p.drawLine(QPointF(cx2 - 1, cy2 + 3), QPointF(cx2 + 5, cy2 - 4))
            else:
                _fill_circle(p, cx2, cy2, cr2, _qc(OVERLAY, 0.4))
                _stroke_circle(p, cx2, cy2, cr2, _qc(OVERLAY, 0.8), 1.5)

            if htype == 'checkbox':
                self._habit_rects.append((cx2 - cr2, cy2 - cr2, cr2 * 2, cr2 * 2, hid, 'toggle', period))

            btn_top = y + 8
            sbw, sbh = 24, 20
            sbx = ix + iw - sbw - 8
            sby = btn_top
            _fill_rrect(p, sbx, sby, sbw, sbh, 5, _qc(MAUVE, 0.15))
            _stroke_rrect(p, sbx, sby, sbw, sbh, 5, _qc(MAUVE, 0.4), 1)
            pcx, pcy = sbx + sbw // 2, sby + sbh // 2
            pen_path = QPainterPath()
            pen_path.moveTo(pcx - 5, pcy + 4); pen_path.lineTo(pcx + 3, pcy - 4)
            pen_path.lineTo(pcx + 5, pcy - 2); pen_path.lineTo(pcx - 3, pcy + 6)
            pen_path.closeSubpath()
            p.setPen(Qt.NoPen); p.setBrush(_qc(MAUVE, 0.9)); p.drawPath(pen_path)
            pen2 = QPen(_qc(MAUVE, 0.5)); pen2.setWidthF(0.8); p.setPen(pen2)
            p.drawLine(QPointF(pcx - 5, pcy + 4), QPointF(pcx - 7, pcy + 6))
            p.drawLine(QPointF(pcx - 7, pcy + 6), QPointF(pcx - 3, pcy + 6))
            self._habit_rects.append((sbx, sby, sbw, sbh, hid, 'edit', period))

            del_x = sbx - sbw - 4
            del_y = btn_top
            _fill_rrect(p, del_x, del_y, sbw, sbh, 5, _qc(RED, 0.15))
            _stroke_rrect(p, del_x, del_y, sbw, sbh, 5, _qc(RED, 0.4), 1)
            pen3 = QPen(_qc(RED, 0.85)); pen3.setWidthF(1.5); p.setPen(pen3)
            dcx, dcy = del_x + sbw // 2, del_y + sbh // 2
            p.drawLine(QPointF(dcx - 4, dcy - 4), QPointF(dcx + 4, dcy + 4))
            p.drawLine(QPointF(dcx + 4, dcy - 4), QPointF(dcx - 4, dcy + 4))
            self._habit_rects.append((del_x, del_y, sbw, sbh, hid, 'del', period))

            flame_x = del_x - 36
            flame_y = row1_y - 8
            sc = YELLOW if streak_count >= 7 else GREEN if streak_count >= 3 else SUBTEXT
            flame_path = QPainterPath()
            flame_path.moveTo(flame_x + 5, flame_y + 14)
            flame_path.cubicTo(flame_x, flame_y + 8, flame_x + 2, flame_y + 2, flame_x + 5, flame_y)
            flame_path.cubicTo(flame_x + 5, flame_y + 5, flame_x + 8, flame_y + 3, flame_x + 8, flame_y)
            flame_path.cubicTo(flame_x + 12, flame_y + 4, flame_x + 12, flame_y + 10, flame_x + 10, flame_y + 14)
            flame_path.closeSubpath()
            p.setPen(Qt.NoPen); p.setBrush(_qc(sc, 0.8)); p.drawPath(flame_path)
            _draw_text(p, flame_x + 14, row1_y + 4, str(streak_count), _qc(sc, 0.9), 8, bold=True)

            name_x = cx2 + cr2 + 6
            name_max_w = flame_x - name_x - 4
            _set_font(p, 12, bold=True)
            display_name = name
            while display_name and _text_w(p, display_name) > name_max_w:
                display_name = display_name[:-1]
            if len(display_name) < len(name):
                display_name = display_name[:-2] + '...'
            _draw_text(p, name_x, row1_y + 4, display_name, _qc(TEXT, 0.5 if is_complete else 1.0), 12, bold=True)

            if htype == 'counter':
                ctrl_y = row1_y + 18
                btn_w, btn_h = 28, 20
                total_ctrl_w = btn_w * 2 + 70
                ctrl_start   = ix + (iw - total_ctrl_w) // 2
                dec_x = ctrl_start
                inc_x = ctrl_start + btn_w + 70

                _fill_rrect(p, dec_x, ctrl_y, btn_w, btn_h, 8, _qc(SKY, 0.2))
                _stroke_rrect(p, dec_x, ctrl_y, btn_w, btn_h, 8, _qc(SKY, 0.6), 1)
                _draw_text(p, dec_x + 8, ctrl_y + btn_h - 4, '-', _qc(SKY), 14, bold=True)
                self._habit_rects.append((dec_x, ctrl_y, btn_w, btn_h, hid, 'dec', period))

                val_str = (f'{val}/{goal}' if goal > 0 else str(val)) + (f' {unit}' if unit else '')
                val_str = val_str.strip()
                col = GREEN if is_complete else (MAUVE if val > 0 else SUBTEXT)
                vx = dec_x + btn_w + 4
                _fill_rrect(p, vx, ctrl_y, 62, btn_h, 8, _qc(col, 0.12))
                _set_font(p, 10, bold=True)
                vw = _text_w(p, val_str)
                _draw_text(p, vx + (62 - vw) / 2, ctrl_y + btn_h - 5, val_str, _qc(col, 0.95), 10, bold=True)

                _fill_rrect(p, inc_x, ctrl_y, btn_w, btn_h, 8, _qc(SKY, 0.2))
                _stroke_rrect(p, inc_x, ctrl_y, btn_w, btn_h, 8, _qc(SKY, 0.6), 1)
                _draw_text(p, inc_x + 6, ctrl_y + btn_h - 4, '+', _qc(SKY), 14, bold=True)
                self._habit_rects.append((inc_x, ctrl_y, btn_w, btn_h, hid, 'inc', period))

            if mode == 'daily':
                strip_y  = y + ih - 38
                strip_x  = ix + 8
                strip_w  = iw - 16
                pill_w   = (strip_w - 6) / 7
                pill_h   = 28
                day_names = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

                for di in range(7):
                    day_d    = str(today - timedelta(days=6 - di))
                    day_val  = log.get(hid, {}).get(day_d, 0)
                    is_today = (di == 6)
                    px2      = strip_x + di * (pill_w + 1)
                    py2      = strip_y
                    wd_idx   = (today - timedelta(days=6 - di)).weekday()
                    dlbl     = day_names[wd_idx]
                    num_lbl  = str((today - timedelta(days=6 - di)).day)

                    if day_val:
                        _fill_rrect(p, px2, py2, pill_w - 1, pill_h, 5, _qc(MAUVE, 0.75))
                    elif is_today:
                        _fill_rrect(p, px2, py2, pill_w - 1, pill_h, 5, _qc(OVERLAY, 0.5))
                        _stroke_rrect(p, px2, py2, pill_w - 1, pill_h, 5, _qc(SKY, 0.4), 1)
                    else:
                        _fill_rrect(p, px2, py2, pill_w - 1, pill_h, 5, _qc(OVERLAY, 0.25))

                    _set_font(p, 8, bold=True)
                    nw = _text_w(p, num_lbl)
                    num_col = _qc((BASE if day_val else (SKY if is_today else SUBTEXT)), 1.0 if day_val else 0.7)
                    _draw_text(p, px2 + (pill_w - 1 - nw) / 2, py2 + 11, num_lbl, num_col, 8, bold=True)

                    _set_font(p, 6)
                    dw = _text_w(p, dlbl)
                    day_col = _qc(BASE if day_val else SUBTEXT, 0.9 if day_val else 0.4)
                    _draw_text(p, px2 + (pill_w - 1 - dw) / 2, py2 + pill_h - 2, dlbl, day_col, 6)

                    self._habit_rects.append((px2, py2, pill_w - 1, pill_h, hid,
                                              'toggle' if htype == 'checkbox' else 'inc', period))

            bar_y = y + ih - 6
            p.fillRect(QRectF(ix + 8, bar_y, iw - 16, 3), _qc(OVERLAY, 0.3))
            if pct_done > 0:
                p.fillRect(QRectF(ix + 8, bar_y, (iw - 16) * pct_done, 3), _qc(GREEN if is_complete else MAUVE, 0.7))

            self._habit_rects.append((ix, y, iw, ih, hid, '_row', period))
            self._habit_rects.append((ix + 6, y + 6, int(iw * 0.55), 30, hid, 'detail', period))
            y += ih + 8

    def _calc_streak(self, hid: str, mode: str, log: dict) -> int:
        if mode != 'daily':
            return 0
        if hid in self._streak_cache:
            return self._streak_cache[hid]
        streak = 0
        for i in range(365):
            if log.get(hid, {}).get(str(date.today() - timedelta(days=i)), 0):
                streak += 1
            else:
                break
        self._streak_cache[hid] = streak
        return streak

    def _draw_stats(self, p: QPainter, px, y, pw) -> None:
        from config import HEARTS_MAX, HEART_REPLENISH_COST
        eco        = self._app.economy
        stats      = self._store.stats
        today      = str(date.today())
        today_date = date.today()
        daily      = stats.get('daily', {})
        td         = daily.get(today, {'sessions': 0, 'focus_mins': 0, 'todos_done': 0})
        start_y    = y

        _draw_text(p, px + 10, y + 18, 'Analytics', _qc(TEXT), 15, bold=True)
        streak = self._store.current_streak()
        _draw_text(p, px + 10, y + 32, f'Streak: {streak} days', _qc(SUBTEXT, 0.8), 9)
        y += 44

        _fill_rrect(p, px + 6, y, pw - 12, 90, 12, QColor(33, 31, 46, 247))

        _draw_text(p, px + 14, y + 14, 'LIVES', _qc(SUBTEXT, 0.5), 8, bold=True)

        hearts = eco.hearts
        n_full = int(hearts)
        frac   = hearts - n_full

        hw, hh_s, hgap = 11, 11, 3
        hstart = px + 50

        def _draw_heart(hx, hy, state):
            if state == 'full':
                col, alpha = RED, 0.9
            elif state in ('3q', 'half', '1q'):
                col, alpha = YELLOW, 0.85
            else:
                col, alpha = OVERLAY, 0.3
            p.save()
            p.translate(hx + hw / 2, hy + hh_s / 2)
            s = hw / 2.2
            path = QPainterPath()
            path.moveTo(0, s * .5)
            path.cubicTo(-s * .1, -s * .3, -s, -s * .3, -s, s * .1)
            path.cubicTo(-s, s * .6, 0, s * 1.1, 0, s * 1.1)
            path.cubicTo(0, s * 1.1, s, s * .6, s, s * .1)
            path.cubicTo(s, -s * .3, s * .1, -s * .3, 0, s * .5)
            p.setPen(Qt.NoPen)
            p.setBrush(_qc(col, alpha))
            p.drawPath(path)
            if state == '3q':
                p.fillRect(QRectF(hw / 2 - s, 0, s * 2, s * 1.2), _qc(col, 0.4))
            elif state == 'half':
                p.fillRect(QRectF(hw / 2, -hh_s / 2, s, hh_s), QColor(33, 31, 46, 178))
            elif state == '1q':
                p.fillRect(QRectF(hw / 4, -hh_s / 2, s * 1.5, hh_s), QColor(33, 31, 46, 178))
            p.restore()

        states = []
        for i in range(HEARTS_MAX):
            if i < n_full:
                states.append('full')
            elif i == n_full and frac > 0:
                if frac >= 0.75:   states.append('3q')
                elif frac >= 0.5:  states.append('half')
                else:              states.append('1q')
            else:
                states.append('empty')

        for i, state in enumerate(states):
            row, col_i = i // 5, i % 5
            _draw_heart(hstart + col_i * (hw + hgap), y + 6 + row * (hh_s + 3), state)

        hstr = f'{hearts:.1f}/{HEARTS_MAX}'
        _draw_text(p, px + pw - 60, y + 14, hstr, _qc(SUBTEXT, 0.5), 7)

        can = eco.can_replenish()
        _fill_rrect(p, px + 14, y + 32, pw - 28, 18, 6, _qc(GREEN if can else OVERLAY, 0.15 if can else 0.08))
        _stroke_rrect(p, px + 14, y + 32, pw - 28, 18, 6, _qc(GREEN if can else OVERLAY, 0.4), 1)
        label = (f'Replenish 1 life  -  {HEART_REPLENISH_COST} coins' if can
                 else f'Need {HEART_REPLENISH_COST} coins to replenish')
        _set_font(p, 7.5, bold=True)
        lw = _text_w(p, label)
        _draw_text(p, px + 14 + (pw - 28 - lw) / 2, y + 44, label, _qc(GREEN if can else SUBTEXT, 0.8), 7.5, bold=True)
        self._replenish_rect = (px + 14, y + 32, pw - 28, 18)

        _draw_text(p, px + 14, y + 64, 'COINS', _qc(YELLOW, 0.7), 8, bold=True)
        cstr = str(eco.coins)
        _set_font(p, 18, bold=True)
        cw = _text_w(p, cstr)
        _draw_text(p, px + pw - 20 - cw, y + 76, cstr, _qc(YELLOW), 18, bold=True)
        mult = eco.preview_streak_bonus(streak)
        _draw_text(p, px + 14, y + 76, f'x{mult:.1f} streak multiplier', _qc(SUBTEXT, 0.6), 7.5)
        y += 98

        bond     = self._store.bond
        bond_pct = bond / 100.0
        if bond >= 90:   bond_label, bond_col = 'Soulmate',     (0.40, 0.85, 0.45)
        elif bond >= 75: bond_label, bond_col = 'Companion',    (0.40, 0.85, 0.45)
        elif bond >= 50: bond_label, bond_col = 'Friend',       (0.97, 0.88, 0.30)
        elif bond >= 25: bond_label, bond_col = 'Acquaintance', (1.00, 0.55, 0.15)
        else:            bond_label, bond_col = 'Stranger',     (0.95, 0.25, 0.30)

        cat_name = self._load_settings_file().get('cat_name', 'Buddy')

        _fill_rrect(p, px + 6, y, pw - 12, 52, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 20, cat_name, _qc(TEXT), 12, bold=True)
        _draw_text(p, px + 14, y + 34, bond_label, _qc(bond_col, 0.9), 8)

        bar_x, bar_w, bar_h, bar_y = px + 14, pw - 28, 6, y + 40
        _fill_rrect(p, bar_x, bar_y, bar_w, bar_h, 3, _qc(OVERLAY, 0.5))
        _fill_rrect(p, bar_x, bar_y, max(4, int(bar_w * bond_pct)), bar_h, 3, _qc(bond_col, 0.85))

        _set_font(p, 18, bold=True)
        bstr = str(bond)
        bw_ = _text_w(p, bstr)
        _draw_text(p, px + pw - 20 - bw_, y + 36, bstr, _qc(bond_col, 0.9), 18, bold=True)
        _draw_text(p, px + pw - 20 - bw_, y + 46, '/100', _qc(SUBTEXT, 0.5), 7)
        y += 60

        _fill_rrect(p, px + 6, y, pw - 12, 56, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 14, 'TODAY', _qc(SUBTEXT, 0.5), 8, bold=True)

        def _stat_pill(label, val, col, ox):
            _fill_rrect(p, px + 14 + ox, y + 22, 68, 26, 8, _qc(col, 0.1))
            _set_font(p, 13, bold=True)
            vs = str(val)
            vw = _text_w(p, vs)
            _draw_text(p, px + 14 + ox + (68 - vw) / 2, y + 38, vs, _qc(col, 0.9), 13, bold=True)
            _set_font(p, 6.5)
            lw2 = _text_w(p, label)
            _draw_text(p, px + 14 + ox + (68 - lw2) / 2, y + 46, label, _qc(col, 0.6), 6.5)

        _stat_pill('todos done', td.get('todos_done', 0), SKY,   0)
        _stat_pill('pomodoros',  td.get('sessions', 0),   GREEN, 74)
        _stat_pill('focus mins', td.get('focus_mins', 0), MAUVE, 148)
        y += 64

        hd     = self._store.habits
        habits = hd.get('habits', [])
        log    = hd.get('log', {})

        _fill_rrect(p, px + 6, y, pw - 12, 120, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 14, 'HABIT HEAT MAP  -  last 10 weeks', _qc(SUBTEXT, 0.5), 8, bold=True)

        if habits:
            daily_habits = [h for h in habits if h.get('mode', 'daily') == 'daily']
            n_habits     = len(daily_habits)
            WEEKS        = 10
            cell_w       = max(6, (pw - 36) / WEEKS)
            cell_h       = max(6, min(12, 96 / max(n_habits, 1)))
            hm_x         = px + 14
            hm_y         = y + 22

            for wi in range(WEEKS):
                wx = hm_x + wi * cell_w
                week_start = today_date - timedelta(days=(WEEKS - 1 - wi) * 7 + today_date.weekday())
                for hi2, h in enumerate(daily_habits[:7]):
                    hid   = h['id']
                    goal  = h.get('goal', 0)
                    htype = h.get('type', 'counter')
                    hy    = hm_y + hi2 * (cell_h + 1)

                    completed = 0
                    for d_off in range(7):
                        d_str = str(week_start + timedelta(days=d_off))
                        val   = log.get(hid, {}).get(d_str, 0)
                        if htype == 'checkbox' and val:
                            completed += 1
                        elif htype == 'counter' and goal > 0 and val >= goal:
                            completed += 1
                        elif htype == 'counter' and goal == 0 and val > 0:
                            completed += 1

                    pct2  = completed / 7.0
                    alpha = 0.08 + pct2 * 0.85
                    _fill_rrect(p, wx + 1, hy, cell_w - 2, cell_h, 2, _qc(YELLOW, alpha))

            today_wi = WEEKS - 1
            tx2 = hm_x + today_wi * cell_w
            pen = QPen(_qc(SKY, 0.6)); pen.setWidthF(1)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(tx2, hm_y - 2, cell_w - 1, n_habits * (cell_h + 1)))
        y += 128

        _fill_rrect(p, px + 6, y, pw - 12, 90, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 14, 'WEEKLY FOCUS MINS', _qc(SUBTEXT, 0.5), 8, bold=True)

        WEEKS_BAR  = 8
        bar_area_w = pw - 32
        bar_unit   = bar_area_w / WEEKS_BAR
        bar_max_h  = 56
        bar_base   = y + 78

        week_vals = []
        for wi in range(WEEKS_BAR):
            week_start = today_date - timedelta(days=today_date.weekday() + (WEEKS_BAR - 1 - wi) * 7)
            total_mins = 0
            for d_off in range(7):
                d_str = str(week_start + timedelta(days=d_off))
                total_mins += daily.get(d_str, {}).get('focus_mins', 0)
            week_vals.append(total_mins)

        bar_max = max(week_vals) if any(week_vals) else 1

        for wi, wv in enumerate(week_vals):
            bx2    = px + 14 + wi * bar_unit
            bh2    = max(2, int((wv / bar_max) * bar_max_h)) if wv else 2
            by2    = bar_base - bh2
            is_cur = wi == WEEKS_BAR - 1
            bw2    = bar_unit - 4

            _fill_rrect(p, bx2 + 1, by2, bw2, bh2, min(bw2 // 2, 5),
                       _qc(MAUVE if is_cur else OVERLAY, 0.85 if is_cur else 0.4))

            if wv > 0:
                _set_font(p, 6, bold=True)
                vs = str(wv)
                vw = _text_w(p, vs)
                _draw_text(p, bx2 + 1 + (bw2 - vw) / 2, by2 - 2, vs, _qc(TEXT, 0.7), 6, bold=True)

        y += 98

        _fill_rrect(p, px + 6, y, pw - 12, 90, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 14, 'WEEKDAY BREAKDOWN', _qc(SUBTEXT, 0.5), 8, bold=True)

        day_names  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        day_totals = [0] * 7
        day_counts = [0] * 7
        for d_str, d_data in daily.items():
            try:
                wd = date.fromisoformat(d_str).weekday()
                day_totals[wd] += d_data.get('focus_mins', 0)
                if d_data.get('focus_mins', 0) > 0:
                    day_counts[wd] += 1
            except ValueError:
                pass

        wd_max  = max(day_totals) if any(day_totals) else 1
        wd_unit = (pw - 32) / 7
        wd_base = y + 78
        wd_maxh = 52
        cur_wd  = today_date.weekday()

        for di in range(7):
            bx3 = px + 14 + di * wd_unit
            bh3 = max(2, int((day_totals[di] / wd_max) * wd_maxh)) if day_totals[di] else 2
            by3 = wd_base - bh3
            bw3 = wd_unit - 4
            is_today_wd = di == cur_wd

            _fill_rrect(p, bx3 + 1, by3, bw3, bh3, min(int(bw3) // 2, 5),
                       _qc(YELLOW if is_today_wd else OVERLAY, 0.85 if is_today_wd else 0.4))

            if day_totals[di] > 0:
                _set_font(p, 6, bold=True)
                vs = str(day_totals[di])
                vw = _text_w(p, vs)
                _draw_text(p, bx3 + 1 + (bw3 - vw) / 2, by3 - 2, vs, _qc(TEXT, 0.7), 6, bold=True)

            _set_font(p, 6.5)
            dw = _text_w(p, day_names[di])
            _draw_text(p, bx3 + 1 + (bw3 - dw) / 2, wd_base + 10, day_names[di],
                       _qc(TEXT if is_today_wd else SUBTEXT, 0.8 if is_today_wd else 0.4), 6.5)

        y += 98

        _fill_rrect(p, px + 6, y, pw - 12, 90, 12, QColor(33, 31, 46, 247))
        _draw_text(p, px + 14, y + 14, 'THIS WEEK', _qc(SUBTEXT, 0.5), 8, bold=True)

        week_start_date = today_date - timedelta(days=today_date.weekday())
        w_todos = w_pomos = w_focus = w_habits_done = w_habits_total = 0
        hd2     = self._store.habits
        habits2 = hd2.get('habits', [])
        log2    = hd2.get('log', {})

        for d_off in range(7):
            d_str2 = str(week_start_date + timedelta(days=d_off))
            d_data = daily.get(d_str2, {})
            w_todos += d_data.get('todos_done', 0)
            w_pomos += d_data.get('sessions', 0)
            w_focus += d_data.get('focus_mins', 0)
            for h in habits2:
                if h.get('mode', 'daily') != 'daily':
                    continue
                w_habits_total += 1
                val2   = log2.get(h['id'], {}).get(d_str2, 0)
                goal2  = h.get('goal', 0)
                htype2 = h.get('type', 'counter')
                if htype2 == 'checkbox' and val2: w_habits_done += 1
                elif htype2 == 'counter' and goal2 > 0 and val2 >= goal2: w_habits_done += 1
                elif htype2 == 'counter' and goal2 == 0 and val2 > 0: w_habits_done += 1

        habit_pct = int(w_habits_done / w_habits_total * 100) if w_habits_total > 0 else 0

        def _week_stat(label, val, col2, ox):
            _fill_rrect(p, px + 14 + ox, y + 22, 58, 44, 8, _qc(col2, 0.08))
            _set_font(p, 16, bold=True)
            vs = str(val)
            vw = _text_w(p, vs)
            _draw_text(p, px + 14 + ox + (58 - vw) / 2, y + 48, vs, _qc(col2, 0.9), 16, bold=True)
            _set_font(p, 6.5)
            lw3 = _text_w(p, label)
            _draw_text(p, px + 14 + ox + (58 - lw3) / 2, y + 62, label, _qc(col2, 0.6), 6.5)

        _week_stat('todos',   w_todos, SKY,   0)
        _week_stat('pomos',   w_pomos, GREEN, 64)
        _week_stat('focus m', w_focus, MAUVE, 128)
        _week_stat('habits%', f'{habit_pct}%', YELLOW if habit_pct >= 80 else RED, 192)
        y += 98

        self._stats_content_h = y - start_y + 8

    def _settings_path(self) -> str:
        from config import SETTINGS_FILE
        return SETTINGS_FILE

    def _load_settings_file(self) -> dict:
        import json
        try:
            with open(self._settings_path()) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings_file(self, data: dict) -> None:
        import json
        from data_store import _atomic_write
        _atomic_write(self._settings_path(), json.dumps(data, indent=2))

    def sound_enabled(self, key: str) -> bool:
        return self._load_settings_file().get(f'sound_{key}', True)

    def _draw_settings(self, p: QPainter, px, y, pw) -> None:
        self._settings_rects = {}

        def section(label, sy):
            _draw_text(p, px + 10, sy, label, _qc(MAUVE), 8, bold=True)

        section('CAT NAME', y + 12); y += 20
        cat_name = self._load_settings_file().get('cat_name', 'Buddy')
        _fill_rrect(p, px + 10, y, pw - 20, 24, 6, _qc(OVERLAY, 0.3))
        _stroke_rrect(p, px + 10, y, pw - 20, 24, 6, _qc(MAUVE, 0.3), 1)
        _draw_text(p, px + 18, y + 16, cat_name, _qc(TEXT), 10, bold=True)
        self._settings_rects['cat_name'] = (px + 10, y, pw - 20, 24)
        y += 32

        _divider(p, px, y, pw); y += 10

        section('VOLUME', y + 12); y += 20
        sl_x = px + 10
        sl_w = pw - 60
        sl_y = y + 8
        sl_h = 6
        vol  = self._settings_vol / 100.0

        _fill_rrect(p, sl_x, sl_y, sl_w, sl_h, 3, _qc(OVERLAY, 0.6))
        _fill_rrect(p, sl_x, sl_y, max(6, int(sl_w * vol)), sl_h, 3, _qc(MAUVE, 0.85))

        kx = sl_x + int(sl_w * vol)
        ky = sl_y + sl_h // 2
        _fill_circle(p, kx, ky, 7, _qc(MAUVE))

        vol_str = f'{self._settings_vol}%'
        _set_font(p, 9, bold=True)
        vsw = _text_w(p, vol_str)
        _draw_text(p, px + pw - 12 - vsw, sl_y + 5, vol_str, _qc(TEXT, 0.85), 9, bold=True)

        self._settings_rects['vol_slider'] = (sl_x, sl_y - 10, sl_w, sl_h + 20)
        y += 28

        _divider(p, px, y, pw); y += 10

        section('SOUNDS', y + 10); y += 20
        s = self._load_settings_file()
        toggles = [
            ('success', 'Task / Habit done'),
            ('chime',   'Pomo complete'),
            ('meow',    'Cat meow'),
            ('purr',    'Cat purr'),
            ('pop',     'Panel open/close'),
            ('whoosh',  'Throw'),
            ('thud',    'Landing'),
        ]
        for key, label in toggles:
            enabled = s.get(f'sound_{key}', True)
            bx, by = px + pw - 40, y
            _fill_rrect(p, bx, by, 28, 14, 7, _qc(GREEN if enabled else OVERLAY, 0.75))
            _fill_circle(p, bx + (20 if enabled else 8), by + 7, 5, QColor(255, 255, 255, 242))
            _draw_text(p, px + 14, y + 10, label, _qc(TEXT, 0.7), 8)
            self._settings_rects[f'toggle_{key}'] = (px + 8, y - 2, pw - 16, 18)
            y += 20

        _divider(p, px, y, pw); y += 10

        section('SKIN', y + 10); y += 20
        import os
        from config import SPRITE_DIR
        try:
            skins = sorted([d for d in os.listdir(SPRITE_DIR)
                            if os.path.isdir(os.path.join(SPRITE_DIR, d))])
        except OSError:
            skins = []

        skin_bw = 34
        skin_gap = 5
        for si, skin in enumerate(skins[:6]):
            num = ''.join(filter(str.isdigit, skin))
            bx2 = px + 10 + si * (skin_bw + skin_gap)
            active = str(self._store.skin) == num
            _fill_rrect(p, bx2, y, skin_bw, skin_bw, 6, _qc(MAUVE if active else OVERLAY, 0.5 if active else 0.2))
            _stroke_rrect(p, bx2, y, skin_bw, skin_bw, 6, _qc(MAUVE, 0.9 if active else 0.3), 1.5 if active else 0.8)
            _set_font(p, 10, bold=True)
            ew = _text_w(p, num)
            _draw_text(p, bx2 + (skin_bw - ew) / 2, y + 22, num, _qc(TEXT, 0.9), 10, bold=True)
            self._settings_rects[f'skin_{num}'] = (bx2, y, skin_bw, skin_bw)
        y += skin_bw + 8

        _divider(p, px, y, pw); y += 10

        section('STARTUP', y + 10); y += 20
        run_at_login = s.get('run_at_login', True)
        bx, by = px + pw - 40, y
        _fill_rrect(p, bx, by, 28, 14, 7, _qc(GREEN if run_at_login else OVERLAY, 0.75))
        _fill_circle(p, bx + (20 if run_at_login else 8), by + 7, 5, QColor(255, 255, 255, 242))
        _draw_text(p, px + 14, y + 10, 'Start automatically at login', _qc(TEXT, 0.7), 8)
        self._settings_rects['toggle_run_at_login'] = (px + 8, y - 2, pw - 16, 18)
        y += 20

    def _handle_settings_click(self, ex: float, ey: float) -> None:
        rects = self._settings_rects

        nr = rects.get('cat_name')
        if nr:
            bx, by, bw, bh = nr
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                self.dialog_open = True
                dialog = QDialog(self)
                dialog.setWindowTitle('Cat Name')
                layout = QVBoxLayout(dialog)
                entry = QLineEdit(self._load_settings_file().get('cat_name', 'Buddy'))
                entry.setPlaceholderText('Enter cat name...')
                layout.addWidget(entry)
                buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)
                if dialog.exec() == QDialog.Accepted:
                    name = entry.text().strip() or 'Buddy'
                    s = self._load_settings_file()
                    s['cat_name'] = name
                    self._save_settings_file(s)
                self.dialog_open = False
                self._app.da.queue_draw()
                return

        vr = rects.get('vol_slider')
        if vr:
            bx, by, bw, bh = vr
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                self._vol_dragging = True
                self._settings_vol = int(max(0, min(100, (ex - bx) / bw * 100)))
                s = self._load_settings_file()
                s['volume'] = self._settings_vol
                self._save_settings_file(s)
                import os
                os.environ['BUDDY_VOLUME'] = str(self._settings_vol)
                self._app.da.queue_draw()
                return

        rl = rects.get('toggle_run_at_login')
        if rl:
            bx, by, bw, bh = rl
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                s = self._load_settings_file()
                new_val = not s.get('run_at_login', True)
                s['run_at_login'] = new_val
                self._save_settings_file(s)
                try:
                    import startup
                    startup.set_enabled(new_val)
                except Exception as e:
                    print(f'[settings] run_at_login toggle error: {e}')
                self._app.da.queue_draw()
                return

        for key, rect in list(rects.items()):
            if not key.startswith('toggle_') or key == 'toggle_run_at_login':
                continue
            bx, by, bw, bh = rect
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                sound_key = key[len('toggle_'):]
                s = self._load_settings_file()
                s[f'sound_{sound_key}'] = not s.get(f'sound_{sound_key}', True)
                self._save_settings_file(s)
                self._app.da.queue_draw()
                return

        for key, rect in list(rects.items()):
            if not key.startswith('skin_'):
                continue
            bx, by, bw, bh = rect
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                num = key[len('skin_'):]
                try:
                    skin_n = int(num)
                    self._store.save_skin(skin_n)
                    self._app._sprites.load_skin(skin_n)
                    self._app._sprites.load_deferred(skin_n)
                    self._bubble.say(f'skin {skin_n}!', 2)
                    self._app.da.queue_draw()
                except Exception as e:
                    print(f'[settings] skin error: {e}')
                return

    def _handle_habit_click(self, ex: float, ey: float) -> None:
        if self._long_press_id is not None:
            GLib.source_remove(self._long_press_id)
            self._long_press_id = None
        ar = self._habit_add_rect
        if ar and ar[0] <= ex <= ar[0] + ar[2] and ar[1] <= ey <= ar[1] + ar[3]:
            self._show_add_habit_dialog(); return

        for bx, by, bw, bh, hid, action, period in self._habit_rects:
            if bx <= ex <= bx + bw and by <= ey <= by + bh:
                hd = self._store.habits
                if action == 'detail':
                    self._show_habit_detail(hid)
                    return
                elif action == 'del':
                    hab_name = next((h.get('name', 'Habit') for h in hd.get('habits', []) if h['id'] == hid), 'Habit')
                    self.dialog_open = True
                    resp = QMessageBox.question(
                        self, 'Delete habit?',
                        f'Delete "{hab_name}"?\n\nThis will also remove all its log data.',
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    )
                    self.dialog_open = False
                    if resp != QMessageBox.Yes:
                        return
                    hd['habits'] = [h for h in hd.get('habits', []) if h['id'] != hid]
                    hd.get('log', {}).pop(hid, None)
                elif action == 'edit':
                    hab = next((h for h in hd.get('habits', []) if h['id'] == hid), None)
                    if hab:
                        self._show_edit_habit_dialog(hab)
                    return
                elif action == 'toggle':
                    hd.setdefault('log', {}).setdefault(hid, {})
                    cur     = hd['log'][hid].get(period, 0)
                    new_val = 0 if cur else 1
                    hd['log'][hid][period] = new_val
                    if new_val == 1:
                        hwm_key = f'{period}_hwm'
                        hwm     = hd['log'][hid].get(hwm_key, 0)
                        if hwm < 1:
                            hd['log'][hid][hwm_key] = 1
                            streak = self._store.current_streak()
                            coins  = self._app.economy.earn_habit(streak)
                            self._app.effects.spawn('coins', coins, *self._app.get_win_pos())
                            from utils import _play_sound
                            from config import SUCCESS_FILE
                            _play_sound(SUCCESS_FILE, 'success')
                            _h2 = next((x for x in hd.get('habits', []) if x['id'] == hid), {})
                            h_streak = self._calc_streak(hid, _h2.get('mode', 'daily'),
                                                          self._store.habits.get('log', {}))
                            self._app.check_habit_streak_milestone(hid, h_streak)
                elif action in ('inc', 'dec'):
                    hd.setdefault('log', {}).setdefault(hid, {})
                    cur     = hd['log'][hid].get(period, 0)
                    new_val = max(0, cur + (1 if action == 'inc' else -1))
                    hd['log'][hid][period] = new_val
                    _h = next((x for x in hd.get('habits', []) if x['id'] == hid), {})
                    if action == 'inc':
                        hwm_key = f'{period}_hwm'
                        hwm     = hd['log'][hid].get(hwm_key, 0)
                        if new_val > hwm:
                            hd['log'][hid][hwm_key] = new_val
                            streak = self._store.current_streak()
                            coins  = self._app.economy.earn_habit(streak)
                            self._app.effects.spawn('coins', coins, *self._app.get_win_pos())
                            from utils import _play_sound
                            from config import SUCCESS_FILE
                            _play_sound(SUCCESS_FILE, 'success')
                            h_streak = self._calc_streak(hid, _h.get('mode', 'daily'),
                                                          self._store.habits.get('log', {}))
                            self._app.check_habit_streak_milestone(hid, h_streak)
                self._store.flush_habits()
                self.invalidate_streaks()
                self._app.da.queue_draw()
                return

    def _show_set_value_dialog(self, hid: str, period: str, hab: dict) -> None:
        self.dialog_open = True
        goal = hab.get('goal', 0)
        unit = hab.get('unit', '')
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Set value - {hab.get('name', '')}")
        layout = QVBoxLayout(dialog)
        cur = self._store.habits.get('log', {}).get(hid, {}).get(period, 0)
        hint = f"Current: {cur}" + (f"  Goal: {goal}" if goal else '') + (f"  unit: {unit}" if unit else '')
        layout.addWidget(QLabel(hint))
        spin = QSpinBox()
        spin.setRange(0, max(goal * 5 if goal else 9999, cur + 50))
        spin.setSingleStep(1)
        spin.setValue(cur)
        layout.addWidget(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        spin.setFocus()
        if dialog.exec() == QDialog.Accepted:
            val = spin.value()
            self._store.habits.setdefault('log', {}).setdefault(hid, {})[period] = val
            self._store.flush_habits()
            self.invalidate_streaks()
            self.update()
        self.dialog_open = False

    def _edit_pomo_label(self) -> None:
        self.dialog_open = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Session label')
        layout = QVBoxLayout(dialog)
        entry = QLineEdit(self.pomo_label)
        entry.setPlaceholderText('e.g. Networks chapter 4...')
        entry.setMaxLength(40)
        layout.addWidget(entry)
        buttons = QDialogButtonBox()
        clear_btn = buttons.addButton('Clear', QDialogButtonBox.DestructiveRole)
        buttons.addButton(QDialogButtonBox.Ok)
        result = {'code': 0}
        clear_btn.clicked.connect(lambda: (result.update(code=2), dialog.accept()))
        buttons.accepted.connect(lambda: (result.update(code=1), dialog.accept()))
        layout.addWidget(buttons)
        entry.setFocus()
        dialog.exec()
        if result['code'] == 1:
            self.pomo_label = entry.text().strip()
            self._store.data['_pomo_label'] = self.pomo_label
            self._store.flush_data()
        elif result['code'] == 2:
            self.pomo_label = ''
            self._store.data.pop('_pomo_label', None)
            self._store.flush_data()
        self.dialog_open = False
        self.update()

    def _todo_dialog_fields(self, layout, todo=None):
        entry = QLineEdit()
        entry.setPlaceholderText('What needs doing? Use #tag to tag')
        if todo:
            entry.setText(todo.get('text', ''))
        layout.addWidget(entry)

        layout.addWidget(QLabel('Priority:'))
        prio_combo = QComboBox()
        prio_combo.addItems(['low', 'med', 'high'])
        cur_prio = todo.get('priority', 'med') if todo else 'med'
        prio_combo.setCurrentIndex(['low', 'med', 'high'].index(cur_prio))
        layout.addWidget(prio_combo)

        layout.addWidget(QLabel('Repeat:'))
        recur_combo = QComboBox()
        recur_combo.addItems(['none', 'daily', 'weekly', 'monthly'])
        recur_vals = ['', 'daily', 'weekly', 'monthly']
        cur_recur  = todo.get('recur', '') if todo else ''
        recur_combo.setCurrentIndex(recur_vals.index(cur_recur) if cur_recur in recur_vals else 0)
        layout.addWidget(recur_combo)

        layout.addWidget(QLabel('Due:'))
        due_edit = QDateTimeEdit()
        due_edit.setCalendarPopup(True)
        due_edit.setDisplayFormat('yyyy-MM-dd  hh:mm AP')
        default_dt = datetime.combine(date.today(), datetime.min.time().replace(hour=23, minute=59))
        if todo and todo.get('due'):
            try:
                due_str = todo['due']
                if 'T' in due_str:
                    default_dt = datetime.fromisoformat(due_str)
                else:
                    default_dt = datetime.combine(date.fromisoformat(due_str),
                                                   datetime.min.time().replace(hour=23, minute=59))
            except (ValueError, TypeError):
                pass
        due_edit.setDateTime(QDateTime(default_dt))
        layout.addWidget(due_edit)

        layout.addWidget(QLabel('Subtasks (one per line):'))
        sub_view = QTextEdit()
        sub_view.setFixedHeight(60)
        if todo:
            existing = '\n'.join(s.get('text', '') for s in todo.get('subtasks', []))
            sub_view.setPlainText(existing)
        layout.addWidget(sub_view)

        return entry, prio_combo, recur_combo, due_edit, sub_view

    def _show_add_todo_dialog(self) -> None:
        self.dialog_open = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Add Task')
        dialog.resize(300, 420)
        layout = QVBoxLayout(dialog)
        entry, prio_combo, recur_combo, due_edit, sub_view = self._todo_dialog_fields(layout)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton('Add Task', QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            text = entry.text().strip()
            if text:
                dt = due_edit.dateTime().toPython()
                recur_vals = ['', 'daily', 'weekly', 'monthly']
                recur      = recur_vals[recur_combo.currentIndex()]
                raw_subs   = sub_view.toPlainText()
                subtasks   = [{'text': s.strip(), 'done': False}
                              for s in raw_subs.splitlines() if s.strip()]
                todo = {
                    'id':       int(time.time() * 1000),
                    'text':     text,
                    'due':      dt.strftime('%Y-%m-%dT%H:%M'),
                    'done':     False,
                    'priority': prio_combo.currentText() or 'med',
                    'tags':     _extract_tags(text),
                    'subtasks': subtasks,
                    'recur':    recur,
                }
                self._store.data.setdefault('todos', []).insert(0, todo)
                self._store.flush_data()
                self._bubble.say(f"Added: {_strip_tags(text)[:18]}", 3)
                self._app.da.queue_draw()
        self.dialog_open = False
        self.update()

    def _show_edit_todo_dialog(self, todo: dict) -> None:
        self.dialog_open = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Edit Task')
        dialog.resize(300, 420)
        layout = QVBoxLayout(dialog)
        entry, prio_combo, recur_combo, due_edit, sub_view = self._todo_dialog_fields(layout, todo)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        del_btn  = buttons.addButton('Delete', QDialogButtonBox.DestructiveRole)
        buttons.addButton('Save', QDialogButtonBox.AcceptRole)
        result = {'code': 0}
        del_btn.clicked.connect(lambda: (result.update(code=2), dialog.accept()))
        buttons.accepted.connect(lambda: (result.update(code=1), dialog.accept()))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        if result['code'] == 1:
            text = entry.text().strip()
            if text:
                dt = due_edit.dateTime().toPython()
                recur_vals = ['', 'daily', 'weekly', 'monthly']
                raw_subs   = sub_view.toPlainText()
                old_subs   = {s['text']: s.get('done', False) for s in todo.get('subtasks', [])}
                new_subtasks = []
                for s in raw_subs.splitlines():
                    s = s.strip()
                    if s:
                        new_subtasks.append({'text': s, 'done': old_subs.get(s, False)})
                todo['text']     = text
                todo['due']      = dt.strftime('%Y-%m-%dT%H:%M')
                todo['priority'] = prio_combo.currentText() or 'med'
                todo['recur']    = recur_vals[recur_combo.currentIndex()]
                todo['tags']     = _extract_tags(text)
                todo['subtasks'] = new_subtasks
                self._store.flush_data()
                self._bubble.say('Task updated!', 2)
        elif result['code'] == 2:
            self._store.data['todos'] = [t for t in self._store.data['todos'] if t['id'] != todo['id']]
            self._store.flush_data()
            self._bubble.say('Task deleted.', 2)
        self.dialog_open = False
        self.update()
        self._app.da.queue_draw()

    def _habit_dialog_widgets(self, layout, h=None):
        def lbl(text):
            layout.addWidget(QLabel(text))
        lbl('Name:')
        name_e = QLineEdit()
        name_e.setPlaceholderText('Water, Run, Meditate...')
        if h: name_e.setText(h.get('name', ''))
        layout.addWidget(name_e)

        lbl('Type:')
        type_combo = QComboBox()
        type_combo.addItems(['counter', 'checkbox'])
        type_combo.setCurrentIndex(0 if (not h or h.get('type', 'counter') == 'counter') else 1)
        layout.addWidget(type_combo)

        lbl('Unit (counter only, e.g. cups, mins):')
        unit_e = QLineEdit()
        unit_e.setPlaceholderText('optional')
        if h: unit_e.setText(h.get('unit', ''))
        layout.addWidget(unit_e)

        lbl('Goal (counter only, 0 = running count):')
        goal_e = QLineEdit()
        goal_e.setPlaceholderText('e.g. 8')
        if h: goal_e.setText(str(h.get('goal', 0)))
        layout.addWidget(goal_e)

        lbl('Reset period:')
        mode_combo = QComboBox()
        modes = ['daily', 'weekly', 'monthly', 'running']
        mode_combo.addItems(modes)
        mode_combo.setCurrentIndex(modes.index(h.get('mode', 'daily')) if h else 0)
        layout.addWidget(mode_combo)

        return {'name': name_e, 'type': type_combo, 'unit': unit_e,
                'goal': goal_e, 'mode': mode_combo}

    def _show_habit_detail(self, hid: str) -> None:
        h = next((x for x in self._store.habits.get('habits', []) if x['id'] == hid), None)
        if not h:
            return

        log    = self._store.habits.get('log', {}).get(hid, {})
        name   = h.get('name', 'Habit')
        goal   = h.get('goal', 0)
        htype  = h.get('type', 'counter')
        streak = self._calc_streak(hid, h.get('mode', 'daily'), self._store.habits.get('log', {}))
        today  = date.today()

        dialog = QDialog(self)
        dialog.setWindowTitle(name)
        dialog.resize(280, 320)
        layout = QVBoxLayout(dialog)

        streak_lbl = QLabel(f'<span style="font-size:24px; font-weight:bold;">{streak}</span>  day streak')
        streak_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(streak_lbl)

        best, cur2 = 0, 0
        for i in range(90):
            d_str = str(today - timedelta(days=89 - i))
            val   = log.get(d_str, 0)
            done  = bool(val) if htype == 'checkbox' else (val >= goal if goal > 0 else val > 0)
            if done: cur2 += 1; best = max(best, cur2)
            else:    cur2 = 0
        best_lbl = QLabel(f'Best streak: {best} days')
        best_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(best_lbl)

        grid_lbl = QLabel('Last 30 days:')
        layout.addWidget(grid_lbl)

        class _GridWidget(QWidget):
            def paintEvent(self_, event):
                gp = QPainter(self_)
                gp.setRenderHint(QPainter.Antialiasing)
                cell, gap, completed = 7, 2, 0
                for i in range(30):
                    d_str = str(today - timedelta(days=29 - i))
                    val   = log.get(d_str, 0)
                    done  = bool(val) if htype == 'checkbox' else (val >= goal if goal > 0 else val > 0)
                    if done: completed += 1
                    row, col = i // 10, i % 10
                    x2, y2   = col * (cell + gap), row * (cell + gap)
                    color = QColor(102, 217, 115, 217) if done else QColor(69, 71, 94, 153)
                    gp.fillRect(QRectF(x2, y2, cell, cell), color)
                gp.setPen(QColor(204, 214, 245, 204))
                gp.setFont(QFont('Sans', 9))
                gp.drawText(QPointF(0, 52), f'{completed}/30 days  ({int(completed/30*100)}% completion rate)')
                gp.end()

        grid = _GridWidget()
        grid.setFixedSize(256, 60)
        layout.addWidget(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_add_habit_dialog(self) -> None:
        self.dialog_open = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Add Habit')
        dialog.resize(300, 310)
        layout = QVBoxLayout(dialog)
        w = self._habit_dialog_widgets(layout)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton('Add', QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            name = w['name'].text().strip()
            if name:
                try:    goal = int(w['goal'].text().strip())
                except (ValueError, TypeError): goal = 0
                habit = {
                    'id':   str(int(time.time() * 1000)),
                    'name': name,
                    'type': w['type'].currentText(),
                    'unit': w['unit'].text().strip(),
                    'goal': goal,
                    'mode': w['mode'].currentText(),
                }
                self._store.habits.setdefault('habits', []).append(habit)
                self._store.flush_habits()
                self._bubble.say(f"Tracking: {name[:16]}!", 3)
                self.update()
        self.dialog_open = False

    def _show_edit_habit_dialog(self, hab: dict) -> None:
        self.dialog_open = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Edit Habit')
        dialog.resize(300, 310)
        layout = QVBoxLayout(dialog)
        w = self._habit_dialog_widgets(layout, hab)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        del_btn = buttons.addButton('Delete', QDialogButtonBox.DestructiveRole)
        buttons.addButton('Save', QDialogButtonBox.AcceptRole)
        result = {'code': 0}
        del_btn.clicked.connect(lambda: (result.update(code=2), dialog.accept()))
        buttons.accepted.connect(lambda: (result.update(code=1), dialog.accept()))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        if result['code'] == 1:
            name = w['name'].text().strip()
            if name:
                try:    goal = int(w['goal'].text().strip())
                except (ValueError, TypeError): goal = 0
                hab['name'] = name
                hab['type'] = w['type'].currentText()
                hab['unit'] = w['unit'].text().strip()
                hab['goal'] = goal
                hab['mode'] = w['mode'].currentText()
                self._store.flush_habits()
                self._bubble.say(f"Updated: {name[:16]}!", 3)
        elif result['code'] == 2:
            hid = hab['id']
            self._store.habits['habits'] = [h for h in self._store.habits['habits'] if h['id'] != hid]
            self._store.habits.get('log', {}).pop(hid, None)
            self._store.flush_habits()
            self._bubble.say('Habit removed.', 2)
        self.dialog_open = False
        self.update()
