"""
habit_reminder.py – Smart habit reminder system with jitter and per-type logic.
"""

import random
from datetime import date, datetime

from config import (
    HABIT_REMIND_WINDOW_START, HABIT_REMIND_WINDOW_END,
    HABIT_REMIND_JITTER_MIN, HABIT_REMIND_JITTER_MAX,
    HABIT_CHECKBOX_REMIND_HOUR,
)
from utils import _get_period_key, _play_meow


class HabitReminder:
    """
    Centralized habit reminder system.
    - Counter habits: evenly-spaced reminders across 06:00-21:00 window.
    - Checkbox habits: single reminder at 21:00.
    - Uses minute-level time checks with jitter to avoid robotic repetition.
    - Never spams; tracks last-reminded time per habit.
    """

    def __init__(self, store, bubble, app=None):
        self._store  = store
        self._bubble = bubble
        self._app    = app
        self._last_reminded_at: dict = {}
        self._last_check_minute: int = -1
        self._jitter: dict           = {}
        self._jitter_date: str       = ''

    def _ensure_jitter(self, habits: list) -> None:
        today = str(date.today())
        if self._jitter_date != today:
            self._jitter_date = today
            self._jitter = {}
        for h in habits:
            hid = h['id']
            if hid not in self._jitter:
                self._jitter[hid] = random.randint(
                    HABIT_REMIND_JITTER_MIN, HABIT_REMIND_JITTER_MAX
                )

    def _counter_reminder_minutes(self, goal: int) -> list:
        """Return sorted list of minutes-since-midnight for evenly-spaced counter reminders."""
        if goal <= 0:
            return []
        window_start = HABIT_REMIND_WINDOW_START * 60
        window_end   = HABIT_REMIND_WINDOW_END   * 60
        window_len   = window_end - window_start
        if goal == 1:
            return [window_start + window_len // 2]
        interval = window_len / goal
        return [int(window_start + i * interval) for i in range(goal)]

    def check(self, panel_open: bool) -> None:
        """Called every tick; fires reminders as needed. Safe to call frequently."""
        now    = datetime.now()
        minute = now.hour * 60 + now.minute

        if minute == self._last_check_minute:
            return
        self._last_check_minute = minute

        if panel_open:
            return

        hd     = self._store.habits
        habits = hd.get('habits', [])
        log    = hd.get('log', {})

        if not habits:
            return

        self._ensure_jitter(habits)

        for h in habits:
            hid   = h['id']
            htype = h.get('type', 'counter')
            name  = h.get('name', 'Habit')
            mode  = h.get('mode', 'daily')
            goal  = h.get('goal', 0)

            if mode != 'daily':
                continue

            period = _get_period_key('daily')
            val    = log.get(hid, {}).get(period, 0)
            jitter = self._jitter.get(hid, 0)

            if htype == 'checkbox':
                self._check_checkbox(hid, name, val, minute, jitter)
            elif htype == 'counter':
                self._check_counter(hid, name, val, goal, minute, jitter)

    def _should_remind(self, hid, minute: int, target_minute: int, jitter: int) -> bool:
        effective = target_minute + jitter
        if minute != effective:
            return False
        last = self._last_reminded_at.get(hid)
        if last is not None and last.date() == date.today():
            return False
        return True

    def _fire_reminder(self, hid, msg: str) -> None:
        _play_meow()
        self._bubble.say(msg, 6)
        self._last_reminded_at[hid] = datetime.now()
        # Spawn exclaim overlay on cat
        try:
            self._app.spawn_exclaim()
        except Exception:
            pass

    def _check_checkbox(self, hid, name: str, val, minute: int, jitter: int) -> None:
        if val:
            return
        target = HABIT_CHECKBOX_REMIND_HOUR * 60
        if self._should_remind(hid, minute, target, jitter):
            self._fire_reminder(hid, f"you forgot {name[:20]}…")

    def _check_counter(self, hid, name: str, val, goal: int, minute: int, jitter: int) -> None:
        if goal <= 0 or val >= goal:
            return

        if not (HABIT_REMIND_WINDOW_START * 60 <= minute < HABIT_REMIND_WINDOW_END * 60):
            return

        targets = self._counter_reminder_minutes(goal)
        for target in targets:
            effective = target + jitter
            if minute == effective:
                last = self._last_reminded_at.get((hid, target))
                if last is not None and last.date() == date.today():
                    continue
                if val < goal:
                    _play_meow()
                    self._bubble.say(f"hey… did you do {name[:20]}?", 6)
                    self._last_reminded_at[(hid, target)] = datetime.now()
                break
