"""
cat_controller.py – BuddyApp: top-level Qt window that orchestrates all subsystems.

Ported from the GTK version. What changed:
  - Gtk.Window         -> QWidget (frameless, translucent, always-on-top, Qt.Tool)
  - Cairo drawing      -> QPainter (paintEvent)
  - GdkPixbuf frames   -> QPixmap (via the ported sprite_manager.py)
  - GTK signals        -> Qt event handlers (mousePressEvent, etc.) / signals
  - GLib.timeout_add/idle_add -> glib_compat.GLib (QTimer-backed shim, same API)
  - Gdk.Display monitor geometry -> QGuiApplication.primaryScreen().geometry()

What did NOT change: the mood/behavior/economy/reminder/greeting logic
below is the same Python it always was — none of that depended on GTK,
it was just sitting in a GTK-derived class. Every method here matches
its GTK counterpart 1:1 in name and intent.

What's deliberately stubbed for now (see panel_manager.py, bubble.py,
effects.py, quick_add.py in this package): the panel UI and particle
effects are placeholders pending their own porting passes. bubble.py
(speech/thought bubbles) IS fully ported and working.

Known simplification: the GTK version called
`win.input_shape_combine_region(...)` to define the window's clickable
area, but that region was always set to the *entire* window rect (not
an alpha-based per-pixel mask) — so it was a no-op in practice. Qt
widgets already accept input over their full rect (including
WA_TranslucentBackground areas) with no equivalent call needed, so
`_update_input_shape` below is a deliberate no-op kept only so its call
sites didn't need to change.
"""

import math
import os
import random
import signal
import time
from datetime import date, datetime

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QPixmap, QCursor, QGuiApplication, QColor
from PySide6.QtWidgets import QWidget, QMenu, QApplication

from glib_compat import GLib
from config import (
    BASE_DIR, LOG_FILE, FRAME_SIZE, RENDER_SCALE, TICK_FAST, TICK_IDLE,
    POMO_TICK_MS, REMIND_U_MS, REMIND_G_MS, BOND_SAVE_MS, DEFERRED_LOAD_MS,
    DRAG_THRESH, REST_STATES, MAUVE, RED, SKY, SUBTEXT, YELLOW,
)
from data_store import DataStore
from sprite_manager import SpriteManager
from animation import AnimationController
from physics import PhysicsController
from bubble import BubbleManager
from panel_manager import PanelManager
from habit_reminder import HabitReminder
from quick_add import build_quick_add_window
import startup
from economy import Economy
from effects import EffectsOverlay
from utils import _play_sound
from config import SUCCESS_FILE, POP_FILE, WHOOSH_FILE, THUD_FILE, PURR_FILE
from utils import (
    _days_until, _get_period_key, _strip_tags, _build_todo_from_parsed,
    _play_chime, _play_meow, _notify,
)


def _qcolor(rgb_tuple, alpha=1.0) -> QColor:
    """rgb_tuple is (r, g, b) floats 0-1, matching the Cairo-era config colors."""
    r, g, b = rgb_tuple
    return QColor(int(r * 255), int(g * 255), int(b * 255), int(alpha * 255))


# ═══════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════

class BuddyApp(QWidget):
    """Top-level orchestrator. Coordinates all subsystems via a single tick loop."""

    def __init__(self):
        super().__init__()

        # ── Subsystems ──
        self._store   = DataStore()
        self._sprites = SpriteManager()
        self.anim     = AnimationController(self._sprites)
        self._physics = PhysicsController()
        self._bubble  = BubbleManager()
        self._panel   = PanelManager(self._store, self._bubble, self)
        self._habit_reminder = HabitReminder(self._store, self._bubble, self)
        self.economy  = Economy()
        self.effects  = EffectsOverlay()
        print(f'[economy] loaded — coins: {self.economy.coins} hearts: {self.economy.hearts} damaged_ids: {self.economy.damaged_ids}')
        bonus = self.economy.claim_daily_bonus()
        print(f'[economy] daily bonus: +{bonus} coins -> {self.economy.coins}')
        # ── Startup catch-up eval ──────────────────────────────────────
        # If buddy wasn't running at midnight, run the eval now for any
        # missed days since last_daily was set
        self._startup_catchup_eval()
        # Load saved volume into env on startup
        try:
            import json
            from config import SETTINGS_FILE
            with open(SETTINGS_FILE) as _sf:
                _sv = json.load(_sf).get('volume', 80)
            os.environ['BUDDY_VOLUME'] = str(_sv)
        except Exception:
            os.environ['BUDDY_VOLUME'] = '80'

        print(f'[buddy] Loading sprites (skin {self._store.skin})...')
        if not self._sprites.load_skin(self._store.skin):
            print('[buddy] Could not load sprites - check SPRITE_DIR')

        # Rotate log if oversized
        _log = LOG_FILE
        try:
            if os.path.exists(_log) and os.path.getsize(_log) > 1_000_000:
                os.replace(_log, _log + '.old')
        except OSError:
            pass

        try:
            screen = QGuiApplication.primaryScreen()
            geo    = screen.geometry()
            self._screen_w, self._screen_h = geo.width(), geo.height()
        except Exception:
            self._screen_w, self._screen_h = 1920, 1080

        self._sprites.sprite_size = FRAME_SIZE * RENDER_SCALE
        cat_sz = self._sprites.sprite_size

        # ── Qt window setup ──
        self.setWindowTitle('Buddy')
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._win_w = cat_sz
        self._win_h = cat_sz
        self.resize(self._win_w, self._win_h)
        self.move(self._screen_w - self._win_w - 24, self._screen_h - self._win_h)

        # Kept so existing call sites (self.da.queue_draw(), etc.) work unchanged —
        # there's no separate drawing-area widget in Qt, BuddyApp paints itself.
        self.da = self
        self.setMouseTracking(False)

        # ── Interaction / mood ──
        self._last_interaction  = time.time()
        self._neglect_notified  = False
        self._pet_count         = 0
        self._mood              = 'happy'
        self._mood_timer        = time.time() + random.randint(120, 300)
        self._next_behavior     = time.time() + random.randint(8, 20)

        # ── Drag state ──
        self._press_x          = 0
        self._press_y          = 0
        self._dragging_cat     = False
        self._win_sx           = 0
        self._win_sy           = 0
        self._last_click_time  = 0.0
        self._quick_add_open   = False
        self._press_region     = None

        # ── Meow / sound cooldowns ──
        self._last_meow_time   = 0.0
        self._last_purr_time   = 0.0
        self._drag_meow_time   = 0.0
        self._drag_started     = False

        # ── Dirty / redraw tracking ──
        self._dirty         = True
        self._last_anim_idx = -1
        self._last_state    = ''

        # ── Single tick loop state ──
        self._tick_interval    = TICK_FAST
        self._pomo_counter     = 0
        self._remind_u_counter = 0
        self._remind_g_counter = 0
        self._last_remind_habit = None
        self._remind_turn       = 0
        self._last_date         = str(date.today())

        self._physics_initialized = False

        # ── Start timers (single tick loop + bond save) ──
        self._tick_id       = GLib.timeout_add(TICK_FAST, self._tick)
        self._bond_save_id  = GLib.timeout_add(BOND_SAVE_MS, self._periodic_bond_save)

        self.show()
        GLib.idle_add(self._update_input_shape)
        self._setup_autostart()
        GLib.idle_add(self._setup_hotkey)

        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)

        GLib.idle_add(self._greet_on_start)
        GLib.timeout_add(DEFERRED_LOAD_MS, self._load_deferred_sprites)

    # ═══════════════════════════════════════════════════════════
    #  QT GEOMETRY HELPERS (so the rest of this file reads like the GTK version)
    # ═══════════════════════════════════════════════════════════

    def get_position(self) -> tuple:
        p = self.pos()
        return p.x(), p.y()

    def queue_draw(self) -> None:
        self.update()

    # ═══════════════════════════════════════════════════════════
    #  TICK LOOP
    # ═══════════════════════════════════════════════════════════

    def _tick(self) -> bool:
        if getattr(self, '_shutdown_requested', False):
            self._quitting = True
            self._store.flush_all()
            QApplication.instance().quit()
            return False

        wx, wy = self.get_position()

        if not self._physics_initialized:
            self._physics.reset(self._screen_h, self._win_h)
            self._physics_initialized = True

        # ── Physics ──
        if self._physics.active and not self._dragging_cat:
            new_wx, new_wy, bounced, settled = self._physics.step(
                wx, wy, self._screen_w, self._win_w)
            if bounced:
                _play_sound(THUD_FILE, 'thud')
                self.anim.play_transition(random.choice(['itch', 'scratch']), 'idle')
            if settled:
                self.anim.set_state('idle')
            if bounced or settled or (new_wx != wx) or (new_wy != wy):
                self.move(int(new_wx), int(new_wy))
                wx, wy = new_wx, new_wy
                self._dirty = True

        # ── Animation advance ──
        if self.anim.advance():
            self._dirty = True

        # ── Bubble timers ──
        was_vis, is_vis = self._bubble.tick()
        if was_vis != is_vis:
            if is_vis:
                GLib.idle_add(self._reposition_bubble)
            else:
                GLib.idle_add(self._bubble.hide)
        elif is_vis and (self._physics.active or self._dragging_cat
                         or self.anim.state in ('walk', 'run', 'walk_back')):
            GLib.idle_add(self._reposition_bubble)

        # ── Neglect / sleep ──
        idle_secs = time.time() - self._last_interaction
        if idle_secs > 900 and not self._neglect_notified:
            self._neglect_notified = True
            self._mood = 'neglected'
            self._maybe_meow(min_gap=10.0, chance=1.0)
            GLib.timeout_add(800, lambda: (
                self._bubble.think(random.choice(['...hello?', 'u there?', 'hey... :(', '*stares at you*']), 6)
                or False))
        elif idle_secs > 1800 and self.anim.state not in REST_STATES:
            self.anim.set_state('rest')
            self._bubble.think('zzzz...', 10)

        # ── Walk movement ──
        if self.anim.state in ('walk', 'run', 'walk_back') and not self._physics.active:
            speed    = 4 if self.anim.state == 'run' else 2
            wxn, wyn = self.get_position()
            going_left = self.anim.flip
            at_left    = wxn < 10
            at_right   = wxn + self._win_w > self._screen_w - 10
            hit = (going_left and at_left) or (not going_left and at_right)
            if hit:
                self.anim.flip = not self.anim.flip
                self.anim.set_state('sit')
                def _patrol_turn():
                    if self.anim.state == 'sit' and not self._dragging_cat:
                        action = ('meow' if self._mood == 'neglected' else
                                  random.choice(['lick', 'lick', 'itch'] if self._mood == 'happy'
                                                else ['itch', 'lick', 'meow']))
                        self.anim.play_transition(action, 'walk')
                    return False
                GLib.timeout_add(random.randint(1200, 2800), _patrol_turn)
            else:
                step = -speed if self.anim.flip else speed
                self.move(int(wxn + step), int(wyn))

        # ── Pomo urgency: last 5 min → run ──
        if (self._panel.pomo_running and not self._panel.pomo_is_break
                and self._panel.pomo_remaining <= 300 and self._panel.pomo_remaining > 0
                and self.anim.state == 'walk' and not self._physics.active):
            self.anim.flip = random.choice([True, False])
            self.anim.set_state('run')

        # ── Mood drift ──
        if time.time() > self._mood_timer:
            self._update_mood()
            self._mood_timer = time.time() + random.randint(120, 300)

        # ── Advance effect particles ──
        if self.effects.tick():
            self.da.queue_draw()

        # ── Pulse overdue todos redraw ──
        if (self._panel.open and self._panel.active_tab == 'todo'
                and any(not t.get('done') and _days_until(t.get('due','')) is not None
                        and _days_until(t.get('due','')) < 0
                        for t in self._store.data.get('todos', []))):
            self._panel.queue_draw()

        # ── Occasional purr when bond is high and cat is resting/idle ──
        self._maybe_purr()
        self.check_overdue_damage()

        # ── Midnight day-change: bond decay + cache busts ──
        today_str = str(date.today())
        if today_str != self._last_date:
            self._last_date = today_str
            self._store.bond = max(5, self._store.bond - 1)
            self._store.flush_bond()
            self._store._prune_habit_log()
            self._panel.invalidate_stats()
            self._panel.invalidate_streaks()
            self._midnight_economy_eval()
            self.economy.claim_daily_bonus()
            self._bubble.say('new day!', 3)
            todos     = self._store.data.get('todos', [])
            due_today = [t for t in todos if not t.get('done') and _days_until(t.get('due','')) == 0]
            overdue   = [t for t in todos if not t.get('done') and (_days_until(t.get('due','')) is not None) and (_days_until(t.get('due','')) < 0)]
            def _new_day_nudge():
                if overdue:
                    self._bubble.think(f'{len(overdue)} overdue!', 6)
                elif due_today:
                    self._bubble.think(f'{len(due_today)} due today!', 5)
                return False
            if overdue or due_today:
                GLib.timeout_add(4000, _new_day_nudge)

        # ── Random behavior ──
        if (time.time() > self._next_behavior
                and not self._panel.pomo_running
                and not self._panel.open
                and not self.anim._transition_state
                and not self._physics.active):
            self._random_behavior()
            self._next_behavior = time.time() + random.randint(8, 25)

        # ── Sub-tick counters ──
        ms = self._tick_interval
        pomo_ticks     = max(1, POMO_TICK_MS  // ms)
        remind_u_ticks = max(1, REMIND_U_MS   // ms)
        remind_g_ticks = max(1, REMIND_G_MS   // ms)

        self._pomo_counter     += 1
        self._remind_u_counter += 1
        self._remind_g_counter += 1

        if self._pomo_counter >= pomo_ticks:
            self._pomo_counter = 0
            self._panel.pomo_tick()

        if self._remind_u_counter >= remind_u_ticks:
            self._remind_u_counter = 0
            self._remind_urgent()

        if self._remind_g_counter >= remind_g_ticks:
            self._remind_g_counter = 0
            self._remind_general()

        # ── Smart habit reminder (minute-level, centralized) ──
        self._habit_reminder.check(self._panel.open)

        # ── Selective redraw ──
        cur_state = self.anim.active_state
        frames    = self._sprites.resolve(cur_state)
        cur_idx   = self.anim.frame % len(frames) if frames else 0
        if (cur_state != self._last_state or cur_idx != self._last_anim_idx
                or self._physics.active or self._dirty
                or self._bubble.visible or self._panel.pomo_running):
            self.da.queue_draw()
            self._last_state    = cur_state
            self._last_anim_idx = cur_idx
            self._dirty         = False

        if self._panel.open:
            p = self._panel
            p._todo_scroll_f += (p.todo_scroll - p._todo_scroll_f) * 0.28
            self._panel.queue_draw()

        # ── Adaptive tick rate ──
        active = (self._panel.pomo_running or self._panel.open or self._dragging_cat
                  or self._physics.active
                  or self.anim.state in ('walk', 'run', 'walk_back')
                  or self.anim._transition_state is not None
                  or self._bubble.visible)
        target = TICK_FAST if active else TICK_IDLE

        if target != self._tick_interval:
            self._tick_interval = target
            self._tick_id = GLib.timeout_add(target, self._tick)
            return False
        return True

    def record_interaction(self) -> None:
        self._last_interaction = time.time()
        self._neglect_notified = False

    # ═══════════════════════════════════════════════════════════
    #  DRAWING
    # ═══════════════════════════════════════════════════════════

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_cat(p)
        self.effects.draw(p)
        p.end()

    def _draw_cat(self, p: QPainter) -> None:
        pb    = self.anim.current_pixbuf()
        cat_w = self._win_w
        cat_h = self._win_h
        cx    = cat_w // 2
        cy    = 0

        active = self.anim.active_state
        f      = self.anim.frame

        if active in REST_STATES:
            float_off = math.sin(f * 0.03) * 1.5
        elif active in ('idle', 'sit', 'lick', 'lick2', 'itch', 'scratch', 'meow'):
            float_off = math.sin(f * 0.15) * 3
        elif active in ('walk', 'walk_back'):
            float_off = abs(math.sin(f * 0.5)) * 1.5
        elif active == 'run':
            float_off = abs(math.sin(f * 0.8)) * 2.5
        else:
            float_off = 0

        if not self._physics.active:
            sy       = cy + cat_h - 2
            shadow_r = cat_w * (0.28 - float_off * 0.004)
            alpha    = max(0, 0.18 - float_off * 0.015)
            p.save()
            p.translate(cx, sy)
            p.scale(1.0, 0.2)
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor((0, 0, 0), alpha))
            r = max(shadow_r, cat_w * 0.12)
            p.drawEllipse(QPoint(0, 0), int(r), int(r))
            p.restore()

        if pb is None:
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(MAUVE, 0.8))
            p.drawEllipse(QPoint(cx, cy + cat_h // 2), cat_w // 2, cat_w // 2)
            return

        p.save()
        p.translate(cx, cy + float_off)
        if self.anim.flip:
            p.scale(-1, 1)
        p.drawPixmap(-cat_w // 2, 0, pb)
        p.restore()

        if active == 'run' and not self._physics.active:
            line_dir = 1 if self.anim.flip else -1
            for i, (ly_off, llen, lalpha) in enumerate([
                    (cat_h * 0.35, 18, 0.22),
                    (cat_h * 0.50, 26, 0.18),
                    (cat_h * 0.65, 14, 0.15),
            ]):
                if (f + i * 3) % 6 > 3:
                    continue
                lx_s = cx + line_dir * (cat_w // 2 + 2)
                lx_e = lx_s + line_dir * llen
                ly   = cy + float_off + ly_off
                pen = p.pen()
                pen.setColor(_qcolor(SUBTEXT, lalpha))
                pen.setWidthF(1.5)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(QPoint(int(lx_s), int(ly)), QPoint(int(lx_e), int(ly)))

        if self._panel.pomo_running and not self._panel.pomo_is_break:
            self._draw_clock(p, cx + cat_w // 2 - 16, cy + float_off + cat_h // 2)

        if self._mood == 'neglected' and not self._bubble.visible:
            pulse = 0.5 + 0.5 * math.sin(f * 0.2)
            p.setFont(self._bold_font(13))
            p.setPen(_qcolor(RED, 0.7 * pulse))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance('!!')
            p.drawText(int(cx - tw // 2), int(cy + float_off - 6 + fm.ascent() * 0.3), '!!')
        elif self._mood == 'sleepy' and active in REST_STATES:
            pulse = 0.4 + 0.6 * abs(math.sin(f * 0.04))
            p.setFont(self._italic_font(11))
            p.setPen(_qcolor(SKY, 0.65 * pulse))
            fm = p.fontMetrics()
            p.drawText(int(cx + cat_w // 4), int(cy + float_off - 4 + fm.ascent() * 0.3), 'z')

    def _bold_font(self, size: int):
        from PySide6.QtGui import QFont
        return QFont('Sans', size, QFont.Bold)

    def _italic_font(self, size: int):
        from PySide6.QtGui import QFont
        f = QFont('Sans', size)
        f.setItalic(True)
        return f

    def _draw_clock(self, p: QPainter, x: float, y: float) -> None:
        r = 14
        p.setPen(Qt.NoPen)
        p.setBrush(_qcolor(YELLOW))
        p.drawEllipse(QPoint(int(x), int(y)), r, r)
        pen = p.pen()
        pen.setColor(QColor(26, 26, 38))
        pen.setWidthF(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPoint(int(x), int(y)), r, r)
        secs  = self._panel.pomo_remaining % 60
        angle = (secs / 60) * 2 * math.pi - math.pi / 2
        ex, ey = x + 9 * math.cos(angle), y + 9 * math.sin(angle)
        pen2 = p.pen()
        pen2.setColor(_qcolor(RED))
        pen2.setWidthF(2)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawLine(QPoint(int(x), int(y)), QPoint(int(ex), int(ey)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(26, 26, 38))
        p.drawEllipse(QPoint(int(x), int(y)), 2, 2)

    # ═══════════════════════════════════════════════════════════
    #  INPUT
    # ═══════════════════════════════════════════════════════════

    def _check_bond_milestone(self, old_bond: int, new_bond: int) -> None:
        milestones = {25: 'Acquaintance!', 50: 'Friends now!',
                      75: 'Close companion!', 90: 'Soulmates!'}
        for threshold, msg in milestones.items():
            if old_bond < threshold <= new_bond:
                self._bubble.say(msg, 5)
                self.effects.spawn('note', 0, *self.get_win_pos())
                self.anim.play_transition('dance', 'idle')
                break

    def check_habit_streak_milestone(self, hid: str, streak: int) -> None:
        if streak in (7, 14, 30, 60, 100):
            self._bubble.say(f'{streak} day streak! Keep it up!', 5)
            self.effects.spawn('coins', streak // 7, *self.get_win_pos())
            self.anim.play_transition('dance', 'idle')

    def _maybe_purr(self) -> None:
        if self._physics.active or self._dragging_cat:
            return
        if self.anim.state not in ('idle', 'sit', 'rest', 'dream'):
            return
        if self._store.bond < 60:
            return
        now = time.time()
        if now - self._last_purr_time < 180:
            return
        if random.random() < 0.015:
            self._last_purr_time = now
            _play_sound(PURR_FILE, 'purr')

    def _maybe_meow(self, min_gap: float = 3.0, chance: float = 1.0) -> bool:
        now = time.time()
        if now - self._last_meow_time < min_gap:
            return False
        if random.random() > chance:
            return False
        self._last_meow_time = now
        self.anim.play_transition('meow', 'idle')
        return True

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._show_context_menu(event); return
        if event.button() != Qt.LeftButton:
            return

        local  = event.position()
        region = self._detect_hit_region(local.x(), local.y())
        if not region:
            return

        self.record_interaction()
        gp = event.globalPosition()
        self._press_x = gp.x()
        self._press_y = gp.y()
        wx, wy        = self.get_position()
        self._win_sx  = wx
        self._win_sy  = wy
        self._dragging_cat = False
        self._drag_started = False
        self._physics.stop()
        self._physics.start_drag_tracking(gp.x(), gp.y())
        self._press_region = region

        if region == 'head':
            self._pet_count += 1
            old_b = self._store.bond
            self._store.add_bond(1)
            self._check_bond_milestone(old_b, self._store.bond)
            b = self._store.bond
            if b >= 80:   reactions = ['<3<3<3', 'my fav human!', 'besties~', "purr~", "<3"]
            elif b <= 20: reactions = ['oh.', '...hi', 'okay.']
            else:         reactions = ["hey!", "purr~", "<3", "heehee", "uwu"]
            self._bubble.say(random.choice(reactions), 1.5)
            if self._pet_count % 5 == 0:
                self.anim.play_transition('lick', 'idle')
                if self._store.bond >= 70 and random.random() < 0.4:
                    wx, wy, wh, ww = self.get_win_pos()
                    self.effects.spawn('note', 0, wx, wy, wh, ww)
        elif region == 'tail':
            self._bubble.say(random.choice(["!?!!", "hey!!", "my tail!!", ">:("]), 1.5)
            self.anim.play_transition('itch', 'idle')
        elif region == 'feet':
            self._bubble.say(random.choice(["tickles!", "hehe", "stopp~"]), 1.5)
            self.anim.play_transition('scratch', 'idle')
        elif region == 'body':
            self._pet_count += 1
            self._store.add_bond(1)
            reactions = ['~', 'mmm', 'purr', ':3']
            if self._store.bond >= 60:
                reactions += ['comfortable~', 'cozy', 'hehe~']
            self._bubble.say(random.choice(reactions), 1)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        was_dragging       = self._dragging_cat
        self._dragging_cat = False
        self._reset_cursor()

        if not was_dragging:
            self.record_interaction()
            bubble_txt = self._bubble.thought_text if self._bubble.thought_ticks > 0 else ''
            if any(w in bubble_txt for w in ('due', 'overdue')) and not self._panel.open:
                self._panel.active_tab = 'todo'

            self._panel.open = not self._panel.open
            if self._panel.open:
                self._maybe_meow(min_gap=4.0, chance=0.35)
                _play_sound(POP_FILE, 'pop')
                self._bubble.say("What's up?", 2)
                wx, wy = self.get_position()
                self._panel.show(wx, wy, self._win_w, self._win_h,
                                 self._screen_w, self._screen_h)
            else:
                _play_sound(POP_FILE, 'pop')
                self._panel.hide()
            self.da.queue_draw()
            return

        wx, wy = self.get_position()
        self._physics.apply_throw(wx, wy)
        if self._physics.active:
            _play_sound(WHOOSH_FILE, 'whoosh')
            self._maybe_meow(min_gap=2.0, chance=0.8)
            if not self.anim._transition_state:
                self.anim.set_state('idle')
        else:
            self.anim.set_state('idle')

        GLib.idle_add(self._update_input_shape)
        if self._panel.open:
            wx2, wy2 = self.get_position()
            self._panel.show(wx2, wy2, self._win_w, self._win_h,
                             self._screen_w, self._screen_h)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return

        gp = event.globalPosition()
        dx = gp.x() - self._press_x
        dy = gp.y() - self._press_y

        if not self._dragging_cat and (abs(dx) > DRAG_THRESH or abs(dy) > DRAG_THRESH):
            self._dragging_cat = True
            self._set_grab_cursor()
            if not self._drag_started:
                self._drag_started = True
                self._maybe_meow(min_gap=2.0, chance=0.7)

        if self._dragging_cat:
            new_wx = max(0, min(self._win_sx + dx, self._screen_w - self._win_w))
            new_wy = max(0, min(self._win_sy + dy, self._screen_h - self._win_h))
            self.move(int(new_wx), int(new_wy))
            self._physics.update_drag_velocity(gp.x(), gp.y())
            if self._panel.open:
                self._panel.show(int(new_wx), int(new_wy), self._win_w, self._win_h,
                                 self._screen_w, self._screen_h)
            now = time.time()
            if now - self._drag_meow_time > 2.5 and random.random() < 0.07:
                self._drag_meow_time = now
                self._maybe_meow(min_gap=2.0, chance=1.0)

    def enterEvent(self, event) -> None:
        local  = self.mapFromGlobal(QCursor.pos())
        region = self._detect_hit_region(local.x(), local.y())
        if not region:
            return
        self._set_hand_cursor()
        self.record_interaction()
        if (not self._dragging_cat and not self._physics.active
                and not self._panel.pomo_running
                and not self.anim._transition_state
                and self.anim.state in ('idle', 'sit', 'rest')
                and random.random() < 0.25):
            choice = random.choice(['lick', 'itch', 'meow'])
            if choice == 'meow':
                self._maybe_meow(min_gap=5.0, chance=1.0)
            else:
                self.anim.play_transition(choice, self.anim.state)

    def leaveEvent(self, event) -> None:
        if not self._dragging_cat:
            self._reset_cursor()

    def _detect_hit_region(self, x: float, y: float):
        pb = self.anim.current_pixbuf()
        if pb is None:
            return None
        pw, ph = pb.width(), pb.height()
        cx_    = self._win_w // 2
        draw_x = cx_ - pw // 2
        draw_y = self._win_h - ph
        regions = {
            'head':  (draw_x + pw * 0.25, draw_y,           pw * 0.5,  ph * 0.35),
            'body':  (draw_x + pw * 0.2,  draw_y + ph * 0.3, pw * 0.6, ph * 0.4),
            'tail':  (draw_x + pw * 0.75, draw_y + ph * 0.3, pw * 0.25, ph * 0.4),
            'feet':  (draw_x + pw * 0.2,  draw_y + ph * 0.75, pw * 0.6, ph * 0.25),
        }
        for name, (rx, ry, rw, rh) in regions.items():
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return name
        return None

    def _reposition_bubble(self) -> None:
        wx, wy = self.get_position()
        prect  = self._panel.window_rect
        self._bubble.position(
            wx, wy, self._win_w, self._win_h,
            self._screen_w, self._screen_h,
            self._panel.open, prect
        )

    def _update_input_shape(self) -> None:
        # Deliberate no-op under Qt — see module docstring for why.
        pass

    # ═══════════════════════════════════════════════════════════
    #  ECONOMY / MIDNIGHT EVAL
    # ═══════════════════════════════════════════════════════════

    def _startup_catchup_eval(self) -> None:
        from datetime import date as _d, timedelta as _td
        today      = _d.today()
        last_str   = self.economy._last_daily
        if not last_str:
            return
        try:
            last_date = _d.fromisoformat(last_str)
        except ValueError:
            return

        days_missed = (today - last_date).days
        if days_missed <= 0:
            return

        print(f'[midnight] catchup: {days_missed} day(s) missed since {last_str}')

        for i in range(min(days_missed, 7)):
            eval_date = last_date + _td(days=i)
            self._midnight_economy_eval_for_date(str(eval_date))

    def _midnight_economy_eval_for_date(self, eval_date_str: str) -> None:
        hd      = self._store.habits
        habits  = hd.get('habits', [])
        log     = hd.get('log', {})
        daily   = self._store.stats.get('daily', {})
        td      = daily.get(eval_date_str, {})

        missed = 0
        for h in habits:
            hid   = h['id']
            mode  = h.get('mode', 'daily')
            htype = h.get('type', 'counter')
            goal  = h.get('goal', 0)
            if mode != 'daily':
                continue
            val = log.get(hid, {}).get(eval_date_str, 0)
            if htype == 'checkbox' and not val:
                missed += 1
            elif htype == 'counter' and goal > 0 and val < goal:
                missed += 1

        had_activity  = (td.get('todos_done', 0) > 0 or td.get('sessions', 0) > 0)
        streak_broken = not had_activity and missed > 0

        print(f'[midnight] {eval_date_str}: missed={missed} streak_broken={streak_broken}')

        if missed > 0 or streak_broken:
            dmg = self.economy.damage_missed_habits(missed, streak_broken)
            print(f'[midnight] damage={dmg} hearts now={self.economy.hearts}')

    def spawn_exclaim(self) -> None:
        wx, wy, wh, ww = self.get_win_pos()
        self.effects.spawn('exclaim', 0, wx, wy, wh, ww)

    def get_win_pos(self) -> tuple:
        wx, wy = self.get_position()
        return wx, wy, self._win_h, self._win_w

    def handle_pomo_end(self) -> None:
        _play_chime()
        panel = self._panel
        if not panel.pomo_is_break:
            self._store.record_pomo_session(panel.pomo_focus_mins, panel.pomo_label.strip())
            old_b2 = self._store.bond
            self._store.add_bond(3)
            self._check_bond_milestone(old_b2, self._store.bond)
            panel.invalidate_stats()
            streak = self._store.current_streak()
            coins  = self.economy.earn_pomo(streak)
            self.effects.spawn('coins', coins, *self.get_win_pos())
            _play_chime()
            today_s = self._store.stats.get('daily', {}).get(
                str(date.today()), {}).get('sessions', 0)
            goal = getattr(self._panel, 'pomo_daily_goal', 4)
            if today_s >= goal:
                self._bubble.say(f'Daily goal reached! {goal} sessions!', 5)
                self.effects.spawn('note', 0, *self.get_win_pos())
                self.anim.play_transition('dance', 'idle')
            panel.pomo_is_break  = True
            panel.pomo_remaining = panel.pomo_break_mins * 60
            panel.pomo_total     = panel.pomo_break_mins * 60
            panel.pomo_running   = True

            today_sessions = self._store.stats.get('daily', {}).get(
                str(date.today()), {}).get('sessions', 1)
            if today_sessions == 1:
                lines = ["Session done! Break started~", "First one down!", "Good start~"]
            elif today_sessions >= 4:
                lines = ["Wow, on a roll!", "You're unstoppable!", "Beast mode fr"]
            else:
                lines = ["Session done! Break started~", "Nice work!", "Keep it up~"]
            if self._store.bond >= 70:
                lines += ["proud of u <3", "you're doing so well!"]

            self.anim.play_transition('scratch', 'sit')
            label = panel.pomo_label.strip() or 'Focus session'
            _notify('Buddy - Break time!', f'{label} done. {panel.pomo_break_mins} min break started.')
            self._bubble.think(random.choice(lines), 8)
        else:
            panel.pomo_running   = False
            panel.pomo_is_break  = False
            panel.pomo_remaining = panel.pomo_focus_mins * 60
            panel.pomo_total     = panel.pomo_focus_mins * 60
            self._maybe_meow(min_gap=3.0, chance=1.0)
            _notify('Buddy - Break over!', "Ready when you are. Start a new session!")
            back_lines = ["Break over! Start when ready~", "Ready when you are~", "Back to it?"]
            if self._store.bond >= 60:
                back_lines += ["let's get it!", "time to shine~"]
            self._bubble.think(random.choice(back_lines), 7)
        self.da.queue_draw()

    def _midnight_economy_eval(self) -> None:
        from datetime import date as _date, timedelta as _td
        hd        = self._store.habits
        habits    = hd.get('habits', [])
        log       = hd.get('log', {})
        yesterday = str(_date.today() - _td(days=1))
        daily     = self._store.stats.get('daily', {})
        td        = daily.get(yesterday, {})

        print(f'[midnight] yesterday={yesterday} habits={len(habits)}')
        missed = 0
        for h in habits:
            hid   = h['id']
            mode  = h.get('mode', 'daily')
            htype = h.get('type', 'counter')
            goal  = h.get('goal', 0)
            if mode != 'daily':
                continue
            val = log.get(hid, {}).get(yesterday, 0)
            if htype == 'checkbox' and not val:
                missed += 1
            elif htype == 'counter' and goal > 0 and val < goal:
                missed += 1

        yesterday_entry = daily.get(yesterday, {})
        had_activity    = (yesterday_entry.get('todos_done', 0) > 0
                          or yesterday_entry.get('sessions', 0) > 0)
        streak          = self._store.current_streak()
        streak_broken   = not had_activity and missed > 0
        total_habits    = len([h for h in habits if h.get('mode', 'daily') == 'daily'])

        print(f'[midnight] missed={missed} streak_broken={streak_broken} had_activity={had_activity}')
        if missed > 0:
            dmg = self.economy.damage_missed_habits(missed, streak_broken)
            self.effects.spawn('damage', dmg, *self.get_win_pos(),
                               hearts_remaining=self.economy.hearts)
            self._bubble.say(f'missed {missed} habit(s)...', 5)
        elif streak_broken:
            dmg = self.economy.damage_missed_habits(0, True)
            self.effects.spawn('damage', dmg, *self.get_win_pos(),
                               hearts_remaining=self.economy.hearts)

        print(f'[midnight] hearts after damage={self.economy.hearts}')
        heal_total = 0.0

        if self.economy._days_active % 2 == 0 and self.economy._days_active > 0:
            heal_total += 0.25

        all_habits_done = (missed == 0 and total_habits > 0)
        has_pomo        = td.get('sessions', 0) >= 1
        has_todo        = td.get('todos_done', 0) >= 1
        if all_habits_done and has_pomo and has_todo:
            heal_total += 0.5
            self._bubble.say('perfect day! <3', 4)

        if streak > 0 and streak % 7 == 0:
            heal_total += 0.5
            self._bubble.say(f'{streak} day streak!', 4)

        if heal_total > 0:
            from config import HEARTS_MAX
            self.economy.hearts = min(float(HEARTS_MAX), self.economy.hearts + heal_total)
            self.economy.flush()
            self.effects.spawn('heal', heal_total, *self.get_win_pos(),
                               hearts_remaining=self.economy.hearts)

    def check_overdue_damage(self) -> None:
        todos      = self._store.data.get('todos', [])
        active_ids = {t.get('id') for t in todos if not t.get('done')}
        before = len(self.economy.damaged_ids)
        self.economy.damaged_ids = [i for i in self.economy.damaged_ids if i in active_ids]
        changed = len(self.economy.damaged_ids) != before
        for t in todos:
            if t.get('done'):
                continue
            tid  = t.get('id')
            days = _days_until(t.get('due', ''))
            if days is not None and days < 0 and tid not in self.economy.damaged_ids:
                dmg = self.economy.damage_overdue_todo(t.get('priority', 'med'))
                print(f'[economy] overdue damage: todo={tid} prio={t.get("priority")} dmg={dmg} hearts={self.economy.hearts}')
                self.effects.spawn('damage', dmg, *self.get_win_pos(),
                                   hearts_remaining=self.economy.hearts)
                self.economy.damaged_ids.append(tid)
                changed = True
        if changed:
            self.economy.flush()

    # ═══════════════════════════════════════════════════════════
    #  MOOD / BEHAVIOR
    # ═══════════════════════════════════════════════════════════

    def _update_mood(self) -> None:
        h    = datetime.now().hour
        idle = time.time() - self._last_interaction
        if h >= 22 or h < 6:
            self._mood = 'sleepy'
        elif idle > 900:
            self._mood = 'neglected'
        elif self._pet_count >= 10:
            self._mood = 'happy'
        elif self._pet_count == 0:
            self._mood = random.choice(['happy', 'bored', 'bored'])
        else:
            self._mood = 'happy'

        if self._mood == 'sleepy' and random.random() < 0.4:
            self._bubble.think(random.choice(['zzzz...', '(sleepy)', '...zzz']), 5)
        elif self._mood == 'bored' and random.random() < 0.3:
            self._bubble.think(random.choice(['...', 'hmm.', '(bored)']), 4)
        elif self._mood == 'neglected' and random.random() < 0.35:
            if self._store.bond >= 60:
                self._bubble.think(random.choice(['miss u...', 'come back :(', '*waits*']), 6)
            else:
                self._bubble.think(random.choice(['...', 'hmph.', '(ignored)']), 5)

    def _random_behavior(self) -> None:
        todos   = self._store.data.get('todos', [])
        overdue = [t for t in todos
                   if not t.get('done')
                   and _days_until(t.get('due', '')) is not None
                   and _days_until(t.get('due', '')) < 0]

        if overdue:
            max_overdue = max(abs(_days_until(t.get('due', '')) or 0) for t in overdue)
            if random.random() < min(0.6, 0.15 + max_overdue * 0.08):
                self._maybe_meow(min_gap=30.0, chance=1.0)
                if max_overdue >= 3 and random.random() < 0.5:
                    worst = max(overdue, key=lambda t: abs(_days_until(t.get('due', '')) or 0))
                    self._bubble.think(f'"{worst["text"][:15]}" {max_overdue}d overdue!!', 6)
                return

        h          = datetime.now().hour
        is_night   = h >= 22 or h < 6
        is_morning = 6 <= h < 10
        mood       = self._mood
        bond       = self._store.bond
        is_sitting = self.anim.state == 'sit'
        is_idle    = self.anim.state == 'idle'

        pool = [
            ('idle',      30),
            ('lick',      12 if mood == 'happy' else 6),
            ('lick2',     8),
            ('meow',      8  if mood in ('bored', 'neglected') else 4),
            ('walk',      5  if is_night else 25 if is_morning else 18),
            ('walk_back', 2  if is_night else 8  if is_morning else 6),
            ('run',       1  if is_night else 10 if is_morning else 6),
            ('itch',      8),
            ('scratch',   5),
            ('rest',      25 if is_night else 3  if is_morning else 5),
        ]

        if bond >= 60 and is_idle and random.random() < 0.3:
            wxc, _ = self.get_position()
            screen_cx = self._screen_w // 2 + random.randint(-120, 120)
            cat_cx    = wxc + self._win_w // 2
            self.anim.flip = cat_cx >= screen_cx
            self.anim.set_state('walk')
            return

        if is_sitting: pool.append(('to_idle', 15))
        elif is_idle:  pool.append(('to_sit',  12))

        total = sum(w for _, w in pool)
        r     = random.uniform(0, total)
        chosen = 'idle'
        cum = 0
        for action, weight in pool:
            cum += weight
            if r <= cum:
                chosen = action; break

        if chosen == 'to_idle':
            self.anim.set_state('idle')
        elif chosen == 'to_sit':
            self.anim.set_state('sit')
        elif chosen in ('walk', 'run', 'walk_back'):
            self.anim.flip = random.choice([True, False])
            self.anim.set_state(chosen)
        elif chosen == 'rest':
            self.anim.set_state('rest')
            self._bubble.say(random.choice(["zzz...", "nap time~", "zZz"]), 8)
        elif chosen in ('itch', 'scratch', 'lick', 'lick2', 'meow'):
            self.anim.play_transition(chosen, 'idle')
        else:
            self.anim.set_state(chosen)

    # ═══════════════════════════════════════════════════════════
    #  REMINDERS
    # ═══════════════════════════════════════════════════════════

    def _remind_urgent(self) -> None:
        if self._panel.pomo_running or self._panel.open:
            return
        todos   = self._store.data.get('todos', [])
        overdue = [t for t in todos if not t.get('done')
                   and _days_until(t.get('due', '')) is not None
                   and _days_until(t.get('due', '')) < 0]
        urgent  = [t for t in todos if not t.get('done')
                   and _days_until(t.get('due', '')) is not None
                   and 0 <= _days_until(t.get('due', '')) <= 1]
        if overdue:
            self._bubble.think(f'"{overdue[0]["text"][:16]}" is overdue!', 7)
        elif urgent:
            d   = _days_until(urgent[0].get('due', ''))
            msg = 'Due today: ' if d == 0 else 'Due tmrw: '
            self._bubble.think(f'{msg}"{urgent[0]["text"][:14]}"', 6)

    def _remind_general(self) -> None:
        if self._panel.pomo_running or self._panel.open:
            return

        bond = self._store.bond
        if bond >= 70:
            idle_lines = [
                "you can do anything <3", "rooting for you!", "i'm here if you need~",
                "my favorite human~", "psst... wanna focus?", "one task at a time!",
            ]
        elif bond <= 20:
            idle_lines = ["...", "okay.", "meow.", "fine.", "do your work.", "hmph."]
        else:
            idle_lines = [
                'psst... wanna focus?', 'i believe in you', 'stretch a little?',
                'water break maybe?', 'you got this!', "don't forget to blink",
                "watching over you", 'one task at a time!', "you're doing great!",
                'meow. stay focused.', 'deep breath... ok go!', "let's get it done",
                'hydrate or diedrate.', 'close some browser tabs?', 'small steps, big progress!',
            ]

        self._remind_turn += 1
        if self._remind_turn % 2 == 0:
            nudge = self._get_habit_nudge()
            if nudge:
                self._bubble.think(nudge, 7)
                return

        todos   = self._store.data.get('todos', [])
        pending = [t for t in todos if not t.get('done')]
        high    = [t for t in pending if t.get('priority') == 'high'
                   and (_days_until(t.get('due', '')) is None or _days_until(t.get('due', '')) > 1)]
        if high and random.random() < 0.5:
            self._bubble.think(f'High: "{high[0]["text"][:16]}"', 6)
        elif pending and random.random() < 0.4:
            self._bubble.think(f'{len(pending)} task(s) waiting.', 5)
        else:
            self._bubble.say(random.choice(idle_lines), 5)

    def _get_habit_nudge(self):
        hd     = self._store.habits
        habits = hd.get('habits', [])
        log    = hd.get('log', {})
        if not habits:
            return None
        h_now = datetime.now().hour
        incomplete = []
        for h in habits:
            hid    = h['id']
            period = _get_period_key(h.get('mode', 'daily'))
            val    = log.get(hid, {}).get(period, 0)
            htype  = h.get('type', 'counter')
            goal   = h.get('goal', 0)
            name   = h.get('name', 'Habit')
            if htype == 'checkbox' and not val:
                incomplete.append((name, hid, 0, goal, htype))
            elif htype == 'counter' and goal > 0 and val < goal:
                incomplete.append((name, hid, val, goal, htype))
        if not incomplete:
            return None
        candidates = [h for h in incomplete if h[1] != self._last_remind_habit] or incomplete
        undone     = [h for h in candidates if h[2] == 0]
        chosen     = random.choice(undone if undone else candidates)
        self._last_remind_habit = chosen[1]
        name, hid, val, goal, htype = chosen
        if htype == 'checkbox':
            return f'Still need to: {name[:18]}!' if h_now >= 20 else f'Remember: {name[:20]}?'
        return f'{name[:16]}: {val}/{goal} done'

    # ═══════════════════════════════════════════════════════════
    #  STARTUP / GREETING
    # ═══════════════════════════════════════════════════════════

    def _greet_on_start(self) -> bool:
        h    = datetime.now().hour
        bond = self._store.bond
        if   5 <= h < 9:    pool = ["good morning~", "morning!", "rise and shine!", "mornin'"]
        elif 9 <= h < 12:   pool = ["meow!", "hi there!", "ready to focus?", "let's go!"]
        elif 12 <= h < 14:  pool = ["lunch break?", "meow~", "hey!", "how's it going?"]
        elif 14 <= h < 18:  pool = ["afternoon!", "meow!", "back at it~", "hey!"]
        elif 18 <= h < 22:  pool = ["evening~", "meow!", "winding down?", "hey!"]
        else:               pool = ["it's late...", "still up?", "zzz... oh! hi", "meow~"]
        if bond >= 80: pool += ["<3", "my fav human!", "yay you're here!"]
        elif bond <= 15: pool += ["...oh", "hi.", "meow."]
        self._bubble.say(random.choice(pool), 3)

        todos = self._store.data.get('todos', [])
        due_today  = [t for t in todos if not t.get('done') and _days_until(t.get('due','')) == 0]
        due_tmrw   = [t for t in todos if not t.get('done') and _days_until(t.get('due','')) == 1]
        overdue    = [t for t in todos if not t.get('done') and (_days_until(t.get('due','')) is not None) and (_days_until(t.get('due','')) < 0)]
        def _delayed_nudge():
            if overdue:
                self._bubble.think(f'{len(overdue)} overdue task(s)!', 6)
            elif due_today:
                self._bubble.think(f'{len(due_today)} due today!', 5)
            elif due_tmrw:
                self._bubble.think(f'{len(due_tmrw)} due tomorrow.', 4)
            return False
        if overdue or due_today or due_tmrw:
            GLib.timeout_add(4000, _delayed_nudge)
        return False

    # ═══════════════════════════════════════════════════════════
    #  SKIN SWITCHING
    # ═══════════════════════════════════════════════════════════

    def _load_deferred_sprites(self) -> bool:
        self._sprites.load_deferred(self._store.skin)
        return False

    def _switch_skin(self, n: int) -> None:
        self.anim.play_transition('scratch', 'idle')

        def _do_switch():
            if self._sprites.load_skin(n):
                self._store.save_skin(n)
                self.anim.frame = 0
                self.anim.tick  = 0
                self._dirty     = True
                self.da.queue_draw()
                self._bubble.say(f"Skin {n}! Meow~", 2)
                GLib.timeout_add(1000, lambda: (self._sprites.load_deferred(n) or False))
            else:
                self._bubble.say("Skin not found!", 2)
            return False

        GLib.timeout_add(400, _do_switch)

    # ═══════════════════════════════════════════════════════════
    #  CONTEXT MENU
    # ═══════════════════════════════════════════════════════════

    def _show_context_menu(self, event) -> None:
        menu = QMenu(self)

        skin_menu = menu.addMenu('Change Skin')
        for n in range(1, 7):
            label = f'Cat {n}' + (' [x]' if n == self._store.skin else '')
            skin_menu.addAction(label, lambda sn=n: self._switch_skin(sn))
        menu.addSeparator()

        menu.addAction('Meow!',    lambda: self.anim.play_transition('meow',    'idle'))
        menu.addAction('Groom',    lambda: self.anim.play_transition('lick',    'idle'))
        menu.addAction('Scratch',  lambda: self.anim.play_transition('itch',    'idle'))
        menu.addAction('Stretch',  lambda: self.anim.play_transition('scratch', 'idle'))
        menu.addAction('Walk',     lambda: (setattr(self.anim, 'flip', random.choice([True, False])) or
                                      self.anim.set_state('walk')))
        menu.addAction('Run!',     lambda: (setattr(self.anim, 'flip', random.choice([True, False])) or
                                      self.anim.set_state('run')))
        menu.addAction('Sit',      lambda: self.anim.set_state('sit'))
        menu.addAction('Rest',     lambda: self.anim.set_state('rest'))
        menu.addSeparator()
        menu.addAction('Hide for 30 min', self._hide_30min)
        menu.addSeparator()
        menu.addAction('Quit Buddy', self._quit_buddy)

        menu.exec(event.globalPosition().toPoint())

    def _quit_buddy(self) -> None:
        self._store.flush_all()
        self.anim.play_transition('meow', 'idle')
        self._quitting = True
        GLib.timeout_add(1500, lambda: QApplication.instance().quit())

    def _hide_30min(self) -> None:
        self.hide()
        self._panel.hide()
        GLib.timeout_add(30 * 60 * 1000, self._unhide)

    def _unhide(self) -> bool:
        self.show()
        self._bubble.say("I'm back!", 3)
        return False

    # ═══════════════════════════════════════════════════════════
    #  CURSOR HELPERS
    # ═══════════════════════════════════════════════════════════

    def _set_hand_cursor(self) -> None:
        try:
            self.setCursor(Qt.OpenHandCursor)
        except Exception:
            pass

    def _set_grab_cursor(self) -> None:
        try:
            self.setCursor(Qt.ClosedHandCursor)
        except Exception:
            pass

    def _reset_cursor(self) -> None:
        try:
            self.unsetCursor()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  PERIODIC SAVES / SIGNAL
    # ═══════════════════════════════════════════════════════════

    def _periodic_bond_save(self) -> bool:
        self._store.flush_bond()
        return True

    def _on_sigterm(self, signum, frame) -> None:
        # Only sets a flag — deliberately does nothing else here. Calling
        # Qt/store APIs directly from inside a true OS signal handler
        # turned out to be unreliable in testing; _tick() (a normal,
        # safe Qt-callback context) picks this up and does the real
        # shutdown work within ~1 tick.
        self._shutdown_requested = True

    def closeEvent(self, event) -> None:
        # Mirrors the GTK version's delete-event handler: this window is
        # only ever meant to close via _quit_buddy()/SIGTERM, not an
        # unsolicited WM close request (it has no titlebar/close button
        # anyway, but session-end / Alt+F4-equivalent could still try).
        #
        # IMPORTANT: QApplication.quit() asks open top-level windows to
        # close as part of its own shutdown sequence — if this always
        # ignores the close request, quit() never actually completes and
        # app.exec() blocks forever, even though quit() itself returns
        # normally. self._quitting (set by _quit_buddy() and the tick
        # loop's shutdown path below) distinguishes "we're deliberately
        # quitting" from "something else tried to close this window."
        if getattr(self, '_quitting', False):
            event.accept()
        else:
            event.ignore()

    # ═══════════════════════════════════════════════════════════
    #  AUTOSTART / HOTKEY
    # ═══════════════════════════════════════════════════════════

    def _setup_autostart(self) -> None:
        try:
            import json
            from config import SETTINGS_FILE
            try:
                with open(SETTINGS_FILE) as f:
                    settings = json.load(f)
            except Exception:
                settings = {}
            want = settings.get('run_at_login', True)
            startup.set_enabled(want)
        except Exception as e:
            print(f'[buddy] Autostart setup failed: {e}')

    def _setup_hotkey(self) -> None:
        # Global hotkey grab stays disabled — see the GTK version's notes;
        # the same unreliability applies (arguably worse, cross-toolkit
        # global hotkeys need a platform-native solution either way, not
        # something Qt solves for free). Quick-add remains available via
        # the same command used to launch Buddy itself, plus --quick-add.
        print('[buddy] Global hotkey unavailable.')
        cmd = ' '.join(startup._run_command())
        print(f'[buddy] Bind compositor shortcut to: {cmd} --quick-add')

    def _quick_add_task(self) -> bool:
        if self._quick_add_open:
            return False
        self._quick_add_open = True

        def on_save(parsed: dict, text: str) -> None:
            todo = _build_todo_from_parsed(parsed, text)
            self._store.data.setdefault('todos', []).insert(0, todo)
            self._store.flush_data()
            flag    = ' [HIGH]' if parsed['priority'] == 'high' else ''
            due_str = f' due {parsed["due"]}' if parsed['due'] else ''
            self._bubble.say(f"Added{flag}: {_strip_tags(text)[:14]}{due_str}", 3)
            self.anim.play_transition('lick', 'idle')
            self._panel.invalidate_stats()

        def on_close() -> None:
            self._quick_add_open = False

        build_quick_add_window(
            screen_w=self._screen_w, screen_h=self._screen_h,
            on_save=on_save, on_close=on_close,
        )
        return False
