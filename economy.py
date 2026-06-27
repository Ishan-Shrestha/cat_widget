"""
economy.py – Coin and heart economy: earning, damage, replenishing, persistence.
"""

import json
import math
from datetime import date

from config import (
    ECONOMY_FILE,
    HEARTS_MAX, HEART_REPLENISH_COST,
    COIN_TODO_EASY, COIN_TODO_MED, COIN_TODO_HARD,
    COIN_POMO, COIN_HABIT, COIN_DAILY_BONUS,
    STREAK_MULTIPLIER,
    DAMAGE_TODO_OVERDUE, DAMAGE_HABIT_MISS,
    DAMAGE_STREAK_BROKEN,
)
from data_store import _atomic_write, _load_json


_PRIO_COIN = {'easy': COIN_TODO_EASY, 'med': COIN_TODO_MED, 'hard': COIN_TODO_HARD}
_PRIO_DMG  = {'low': 1.0, 'med': 1.0, 'high': 2.0}


class Economy:
    """
    Manages coins and hearts (9 lives).
    Hearts are stored as a float so half-heart damage is precise.
    Coins are integers.
    """

    def __init__(self):
        raw               = _load_json(ECONOMY_FILE, {})
        self.coins        = int(raw.get('coins', 0))
        self.hearts       = float(raw.get('hearts', float(HEARTS_MAX)))
        self._last_daily  = raw.get('last_daily', '')
        self._days_active = int(raw.get('days_active', 0))
        self.damaged_ids  = raw.get('damaged_ids', [])
        self._pending: list[dict] = []   # queued effect events for effects.py to consume

    # ── Streak multiplier ──────────────────────────────────────────────────

    def _streak_mult(self, streak: int) -> float:
        """1.0 base + 0.5 per completed 7-day week, capped at 2.0."""
        weeks = streak // 7
        return min(2.0, 1.0 + weeks * STREAK_MULTIPLIER)

    # ── Earning ────────────────────────────────────────────────────────────

    def earn_todo(self, priority: str, streak: int) -> int:
        base  = _PRIO_COIN.get(priority, COIN_TODO_MED)
        coins = max(1, round(base * self._streak_mult(streak)))
        self._add_coins(coins)
        self._pending.append({'type': 'coins', 'amount': coins})
        self.flush()
        return coins

    def earn_pomo(self, streak: int) -> int:
        coins = max(1, round(COIN_POMO * self._streak_mult(streak)))
        self._add_coins(coins)
        self._pending.append({'type': 'coins', 'amount': coins})
        self.flush()
        return coins

    def earn_habit(self, streak: int) -> int:
        coins = max(1, round(COIN_HABIT * self._streak_mult(streak)))
        self._add_coins(coins)
        self._pending.append({'type': 'coins', 'amount': coins})
        self.flush()
        return coins

    def claim_daily_bonus(self) -> int:
        today = str(date.today())
        if self._last_daily == today:
            return 0
        self._last_daily  = today
        self._days_active += 1
        self._add_coins(COIN_DAILY_BONUS)
        self._pending.append({'type': 'coins', 'amount': COIN_DAILY_BONUS})
        self.flush()
        return COIN_DAILY_BONUS

    # ── Damage ────────────────────────────────────────────────────────────

    def damage_overdue_todo(self, priority: str) -> float:
        dmg = DAMAGE_TODO_OVERDUE * _PRIO_DMG.get(priority, 1.0)
        return self._apply_damage(dmg)

    def damage_missed_habits(self, count: int, streak_broken: bool) -> float:
        dmg = count * DAMAGE_HABIT_MISS
        if streak_broken:
            dmg += DAMAGE_STREAK_BROKEN
        if dmg <= 0:
            return 0.0
        return self._apply_damage(dmg)


    def _apply_damage(self, dmg: float) -> float:
        if dmg <= 0:
            return 0.0                   # no damage — don't touch hearts
        dmg = round(max(0.25, dmg), 2)  # keep precision, min 0.25
        dmg = min(dmg, 3.0)             # cap single event at 3 hearts
        self.hearts = max(0.0, self.hearts - dmg)
        self._pending.append({'type': 'damage', 'amount': dmg})
        self.flush()
        return dmg

    # ── Replenish ─────────────────────────────────────────────────────────

    def can_replenish(self) -> bool:
        return self.coins >= HEART_REPLENISH_COST and self.hearts < HEARTS_MAX

    def replenish_heart(self) -> bool:
        if not self.can_replenish():
            return False
        self.coins  -= HEART_REPLENISH_COST
        self.hearts  = min(float(HEARTS_MAX), self.hearts + 1.0)
        self._pending.append({'type': 'heal', 'amount': 1.0})
        self.flush()
        return True

    # ── Coin preview (for stats panel) ───────────────────────────────────

    def preview_streak_bonus(self, streak: int) -> float:
        """Return current streak multiplier for display in stats panel."""
        return self._streak_mult(streak)

    # ── Effect event queue ────────────────────────────────────────────────

    def pop_events(self) -> list[dict]:
        """Consume and return all pending effect events."""
        evts          = self._pending[:]
        self._pending = []
        return evts

    # ── Persistence ───────────────────────────────────────────────────────

    def _add_coins(self, n: int) -> None:
        before = self.coins
        self.coins = max(0, self.coins + n)
        print(f'[economy] coins: {before} -> {self.coins} (delta={n})')

    def flush(self) -> None:
        import traceback
        print(f'[economy] flush — coins={self.coins} hearts={self.hearts}')
        _atomic_write(ECONOMY_FILE, json.dumps({
            'coins':       self.coins,
            'hearts':      self.hearts,
            'last_daily':  self._last_daily,
            'days_active': self._days_active,
            'damaged_ids': self.damaged_ids,
        }, indent=2))
