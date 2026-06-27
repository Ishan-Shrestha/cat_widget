"""
utils.py – Tag utilities, todo parsers, date helpers, and audio/notify.

Ported from the GTK version: the Cairo-drawing helpers (_rrect, _divider)
were removed from here since they're panel_manager.py-only and will get
reimplemented with QPainter when that file is ported. Everything else
below was already plain Python with no GTK dependency.
"""

import calendar
import os
import random
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from config import RECUR_NONE, RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY


# ── Drawing helpers (QPainter) ──
# Cairo-era panel_manager.py built a path with _rrect(cr, ...) then called
# cr.fill()/cr.stroke() on it separately. QPainter doesn't have a freestanding
# "current path" the same way, so _rrect returns a QPainterPath instead —
# call sites become painter.fillPath(path, color) / painter.drawPath(path)
# (with a pen set, for a stroke) instead of cr.fill()/cr.stroke().

def _rrect(x: float, y: float, w: float, h: float, r: float):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.addRoundedRect(x, y, w, h, r, r)
    return path


def _divider(painter, x: float, y: float, w: float) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor
    from config import OVERLAY
    pen = painter.pen()
    pen.setColor(QColor(int(OVERLAY[0]*255), int(OVERLAY[1]*255), int(OVERLAY[2]*255), int(0.4*255)))
    pen.setWidthF(1)
    painter.setPen(pen)
    painter.drawLine(QPointF(x + 4, y), QPointF(x + w - 4, y))


def _qc(rgb_tuple, alpha=1.0):
    """rgb_tuple is (r,g,b) floats 0-1 (Cairo-era config colors) -> QColor."""
    from PySide6.QtGui import QColor
    r, g, b = rgb_tuple
    return QColor(int(r * 255), int(g * 255), int(b * 255), int(alpha * 255))

# ── Date helpers ──

def _days_until(due: str):
    # Returns fractional days until due. Negative = overdue.
    # Handles both date-only (2025-01-01) and datetime (2025-01-01T14:30) strings.
    if not due:
        return None
    try:
        now = datetime.now()
        if 'T' in due:
            due_dt = datetime.strptime(due, '%Y-%m-%dT%H:%M')
        else:
            # Date-only: treat as overdue after 23:59 that day
            d = datetime.strptime(due, '%Y-%m-%d')
            due_dt = d.replace(hour=23, minute=59)
        return (due_dt - now).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def _get_period_key(mode: str) -> str:
    today = date.today()
    if mode == 'daily':   return str(today)
    if mode == 'weekly':  return today.strftime('%Y-W%W')
    if mode == 'monthly': return today.strftime('%Y-%m')
    return 'running'


# ── Tag utilities ──

def _extract_tags(text: str) -> list:
    """Return list of #tags found in text (lowercased, no #)."""
    return [m.lower() for m in re.findall(r'#(\w+)', text)]


def _strip_tags(text: str) -> str:
    """Remove #tag tokens from display text."""
    return re.sub(r'\s*#\w+', '', text).strip()


def _tag_color(tag: str) -> tuple:
    """Deterministic color for a tag string."""
    from config import TAG_COLORS
    return TAG_COLORS[hash(tag) % len(TAG_COLORS)]


# ── Smart quick-add parser ──

def _parse_quick_add(raw: str) -> dict:
    """
    Parse quick-add syntax into a todo dict.
    Supports:
      !text           → high priority
      !!text          → high priority (alt)
      ~text           → low priority
      /today          → due today
      /tomorrow /tmr  → due tomorrow
      /mon /tue ...   → due next weekday
      /N              → due in N days
      #tag            → tags list
    Returns partial todo dict (no id/done).
    """
    text     = raw.strip()
    priority = 'med'
    due      = ''

    if text.startswith('!!'):
        priority = 'high'
        text     = text[2:].lstrip()
    elif text.startswith('!'):
        priority = 'high'
        text     = text[1:].lstrip()
    elif text.startswith('~'):
        priority = 'low'
        text     = text[1:].lstrip()

    weekdays = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
    due_pat  = re.compile(r'\s*/(\S+)', re.IGNORECASE)
    match    = due_pat.search(text)
    if match:
        token = match.group(1).lower()
        today = date.today()
        if token in ('today', 'tod'):
            due = str(today)
        elif token in ('tomorrow', 'tmr', 'tom'):
            due = str(today + timedelta(days=1))
        elif token in weekdays:
            target_wd  = weekdays[token]
            days_ahead = (target_wd - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            due = str(today + timedelta(days=days_ahead))
        else:
            try:
                due = str(today + timedelta(days=int(token)))
            except ValueError:
                pass
        text = due_pat.sub('', text).strip()

    tags = _extract_tags(text)

    return {
        'text':     text,
        'priority': priority,
        'due':      due,
        'tags':     tags,
        'subtasks': [],
        'recur':    RECUR_NONE,
    }


# ── Recurring task engine ──

def _recur_next_due(due_str: str, recur: str) -> str:
    """Compute next due date for a recurring task after completion."""
    try:
        d = date.fromisoformat(due_str) if due_str else date.today()
    except ValueError:
        d = date.today()
    if recur == RECUR_DAILY:
        return str(d + timedelta(days=1))
    if recur == RECUR_WEEKLY:
        return str(d + timedelta(weeks=1))
    if recur == RECUR_MONTHLY:
        m  = d.month + 1
        yr = d.year + (m - 1) // 12
        m  = ((m - 1) % 12) + 1
        dy = min(d.day, calendar.monthrange(yr, m)[1])
        return str(date(yr, m, dy))
    return ''


def _spawn_recur_task(todo: dict) -> dict:
    """Create the next instance of a recurring task."""
    return {
        'id':       int(time.time() * 1000) + random.randint(1, 999),
        'text':     todo['text'],
        'due':      _recur_next_due(todo.get('due', ''), todo.get('recur', '')),
        'done':     False,
        'priority': todo.get('priority', 'med'),
        'tags':     todo.get('tags', []),
        'subtasks': [{'text': s['text'], 'done': False}
                     for s in todo.get('subtasks', [])],
        'recur':    todo.get('recur', ''),
    }


def _build_todo_from_parsed(parsed: dict, text: str) -> dict:
    """Construct a fresh todo dict from a _parse_quick_add result."""
    return {
        'id':       int(time.time() * 1000),
        'text':     text,
        'due':      parsed['due'],
        'done':     False,
        'priority': parsed['priority'],
        'tags':     parsed['tags'],
        'subtasks': [],
        'recur':    '',
    }


# ── Notifications & audio ──
#
# Both were OS-shell-out calls in the GTK version (notify-send, ffplay/
# aplay) — Linux-only tools with no Windows/macOS equivalent. Audio is
# now QSoundEffect (ships with PySide6/Qt itself, genuinely cross-
# platform, no external binary needed). Notifications are still a
# best-effort per-OS shell-out: Linux keeps notify-send, macOS gets a
# real implementation via osascript, Windows is a known gap for now —
# it silently no-ops rather than guessing at an untested toast pipeline.

def _notify(title: str, body: str) -> None:
    try:
        if sys.platform == 'darwin':
            # osascript's AppleScript string literals: escape quotes/backslashes.
            def _esc(s: str) -> str:
                return s.replace('\\', '\\\\').replace('"', '\\"')
            script = f'display notification "{_esc(body)}" with title "{_esc(title)}"'
            subprocess.Popen(['osascript', '-e', script],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith('win'):
            pass  # TODO: native Windows toast — not yet implemented, no-op for now.
        else:
            subprocess.Popen(['notify-send', '-a', 'Buddy', '-t', '8000', title, body],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


_sound_cache = {}  # path -> QSoundEffect, reused so we're not re-decoding every play


def _play_sound(path: str, sound_key: str = '') -> None:
    """Non-blocking audio playback with volume and per-sound toggle support."""
    if not os.path.exists(path):
        return
    # Check per-sound toggle
    if sound_key:
        try:
            import json
            from config import SETTINGS_FILE
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
            if not s.get(f'sound_{sound_key}', True):
                return
        except Exception:
            pass
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
        effect = _sound_cache.get(path)
        if effect is None:
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(path))
            _sound_cache[path] = effect
        vol = int(os.environ.get('BUDDY_VOLUME', '80'))
        effect.setVolume(max(0.0, min(1.0, vol / 100.0)))
        effect.play()
    except Exception as e:
        print(f'[sound] playback error: {e}')


def _play_chime() -> None:
    from config import CHIME_FILE
    _play_sound(CHIME_FILE, 'chime')


def _play_meow() -> None:
    from config import MEOW_FILE
    _play_sound(MEOW_FILE, 'meow')
