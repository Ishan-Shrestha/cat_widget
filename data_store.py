"""
data_store.py – JSON persistence layer with atomic writes and corruption safety.
"""

import json
import os
import threading
from datetime import date, datetime, timedelta

from config import (
    DATA_FILE, STATS_FILE, HABITS_FILE, SKIN_FILE, BOND_FILE, GDRIVE_STATS
)


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, path)


def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


class DataStore:
    """Handles all JSON persistence with atomic writes and corruption safety."""

    def __init__(self):
        self._dirty_data  = False
        self._last_flush  = 0.0

        self.data   = _load_json(DATA_FILE,  {'todos': []})
        self.stats  = _load_json(STATS_FILE, {
            'sessions': [], 'daily': {}, 'todos_completed': 0, 'total_focus_mins': 0
        })
        self.habits = _load_json(HABITS_FILE, {'habits': [], 'log': {}})
        self.skin   = _load_json(SKIN_FILE,  {}).get('skin', 1)

        bond_raw = _load_json(BOND_FILE, {'bond': 20, 'last_seen': str(date.today())})
        bond_val = max(0, min(100, bond_raw.get('bond', 20)))
        try:
            days_gone = (date.today() - date.fromisoformat(
                bond_raw.get('last_seen', str(date.today())))).days
            bond_val = max(5, bond_val - days_gone)   # soft floor: never below 5
        except (ValueError, TypeError):
            pass
        self.bond = bond_val

        # Prune habit log older than 90 days on startup
        self._prune_habit_log()

    def _prune_habit_log(self) -> None:
        """Remove daily log entries older than 90 days to keep JSON lean."""
        cutoff  = str(date.today() - timedelta(days=90))
        log     = self.habits.get('log', {})
        changed = False
        for hid in list(log.keys()):
            old_keys = [k for k in log[hid] if len(k) == 10 and k < cutoff]
            for k in old_keys:
                del log[hid][k]
                changed = True
        if changed:
            self.flush_habits()

    def mark_data_dirty(self):
        self._dirty_data = True

    def flush_data(self):
        _atomic_write(DATA_FILE, json.dumps(self.data))
        self._dirty_data = False

    def record_pomo_session(self, focus_mins: int, label: str = '') -> None:
        today = str(date.today())
        self.stats.setdefault('daily', {})
        self.stats['daily'].setdefault(today, {'sessions': 0, 'focus_mins': 0, 'todos_done': 0})
        self.stats['daily'][today]['sessions']   += 1
        self.stats['daily'][today]['focus_mins'] += focus_mins
        self.stats['total_focus_mins']            = self.stats.get('total_focus_mins', 0) + focus_mins
        entry = {'date': today, 'time': datetime.now().strftime('%H:%M'), 'mins': focus_mins}
        if label:
            entry['label'] = label
        self.stats.setdefault('sessions', []).append(entry)
        self.stats['sessions'] = self.stats['sessions'][-90:]
        self._flush_stats()

    def record_todo_done(self) -> None:
        today = str(date.today())
        self.stats.setdefault('daily', {})
        self.stats['daily'].setdefault(today, {'sessions': 0, 'focus_mins': 0, 'todos_done': 0})
        self.stats['daily'][today]['todos_done'] += 1
        self.stats['todos_completed']             = self.stats.get('todos_completed', 0) + 1
        self._flush_stats()

    def _flush_stats(self) -> None:
        _atomic_write(STATS_FILE, json.dumps(self.stats, indent=2))
        stats_copy = json.dumps(self.stats, indent=2)
        def _gdrive_write():
            try:
                if os.path.isdir(os.path.expanduser('~/GoogleDrive')):
                    os.makedirs(os.path.dirname(GDRIVE_STATS), exist_ok=True)
                    _atomic_write(GDRIVE_STATS, stats_copy)
            except OSError as e:
                print(f'[buddy] GDrive mirror failed: {e}')
        t = threading.Thread(target=_gdrive_write, daemon=True)
        t.start()

    def flush_habits(self) -> None:
        _atomic_write(HABITS_FILE, json.dumps(self.habits, indent=2))

    def save_skin(self, n: int) -> None:
        self.skin = n
        _atomic_write(SKIN_FILE, json.dumps({'skin': n}))

    def add_bond(self, delta: int) -> None:
        self.bond = max(0, min(100, self.bond + delta))
        if self.bond % 10 == 0:
            self.flush_bond()

    def flush_bond(self) -> None:
        try:
            _atomic_write(BOND_FILE, json.dumps({'bond': self.bond, 'last_seen': str(date.today())}))
        except OSError:
            pass

    def flush_all(self) -> None:
        self.flush_data()
        self.flush_bond()

    # ── Economy helpers (called by cat_controller after economy.py acts) ──

    def current_streak(self) -> int:
        """Return the current daily streak based on stats.daily keys."""
        today   = str(__import__('datetime').date.today())
        daily   = self.stats.get('daily', {})
        streak  = 0
        d       = __import__('datetime').date.today()
        while True:
            key = str(d)
            if key not in daily:
                break
            entry = daily[key]
            if entry.get('todos_done', 0) == 0 and entry.get('sessions', 0) == 0:
                break
            streak += 1
            d -= __import__('datetime').timedelta(days=1)
        return streak
