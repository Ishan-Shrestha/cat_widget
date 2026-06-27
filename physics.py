"""
physics.py – Realistic projectile physics for the cat window.
All four edges handled: floor, ceiling, left wall, right wall.
"""

from collections import deque
from config import (
    GRAVITY, BOUNCE_COEF, FRICTION,
    WALL_BOUNCE, THROW_SCALE, FLOOR_SLIDE, VEL_HISTORY
)


class PhysicsController:

    def __init__(self):
        self.active       = False
        self.vel_x        = 0.0
        self.vel_y        = 0.0
        self._pos_x       = 0.0
        self._pos_y       = 0.0
        self._floor_y     = None
        self._ceil_y      = 0.0      # always 0 — top of screen
        self._win_h       = 0

        self._vx_hist     = deque(maxlen=VEL_HISTORY)
        self._vy_hist     = deque(maxlen=VEL_HISTORY)
        self._last_x      = 0.0
        self._last_y      = 0.0
        self._tracking    = False
        self._hit_count   = 0

    def reset(self, screen_h: int, win_h: int) -> None:
        self._win_h   = win_h
        self._floor_y = float(screen_h - win_h)
        self._ceil_y  = 0.0

    def stop(self) -> None:
        self.active = False
        self.vel_x  = 0.0
        self.vel_y  = 0.0

    def start_drag_tracking(self, x: float, y: float) -> None:
        self._vx_hist.clear()
        self._vy_hist.clear()
        self._last_x   = x
        self._last_y   = y
        self._tracking = True

    def update_drag_velocity(self, x: float, y: float) -> None:
        if not self._tracking:
            return
        self._vx_hist.append(x - self._last_x)
        self._vy_hist.append(y - self._last_y)
        self._last_x = x
        self._last_y = y

    def _weighted_velocity(self, hist: deque) -> float:
        if not hist:
            return 0.0
        weights = list(range(1, len(hist) + 1))
        return sum(v * w for v, w in zip(hist, weights)) / sum(weights)

    def apply_throw(self, wx: int, wy: int) -> None:
        self._tracking = False
        floor_y = self._floor_y if self._floor_y is not None else float(wy)

        vx = self._weighted_velocity(self._vx_hist) * THROW_SCALE
        vy = self._weighted_velocity(self._vy_hist) * THROW_SCALE

        vx = max(-32.0, min(32.0, vx))
        vy = max(-32.0, min(32.0, vy))

        if float(wy) < floor_y - 8:
            self.vel_x      = vx
            self.vel_y      = vy
            self._pos_x     = float(wx)
            self._pos_y     = float(wy)
            self._hit_count = 0
            self.active     = True
        elif abs(vx) > FLOOR_SLIDE:
            self.vel_x      = vx * 0.85
            self.vel_y      = 0.0
            self._pos_x     = float(wx)
            self._pos_y     = floor_y
            self.active     = True

    def step(self, wx: int, wy: int, screen_w: int, win_w: int) -> tuple:
        if not self.active or self._floor_y is None:
            return wx, wy, False, False

        floor_y = self._floor_y
        ceil_y  = self._ceil_y
        bounced = False
        settled = False

        # Gravity
        self.vel_y += GRAVITY

        # Advance
        self._pos_x += self.vel_x
        self._pos_y += self.vel_y

        # ── Ceiling ──
        if self._pos_y < ceil_y:
            self._pos_y = ceil_y
            # Reverse vertical velocity downward — ceiling kills upward momentum
            self.vel_y = abs(self.vel_y) * BOUNCE_COEF

        # ── Floor ──
        if self._pos_y >= floor_y:
            self._pos_y     = floor_y
            self._hit_count += 1

            if abs(self.vel_y) > 2.0:
                self.vel_y *= -BOUNCE_COEF
                self.vel_x *= FRICTION
                bounced     = self._hit_count <= 2
            else:
                self.vel_y = 0.0
                self.vel_x *= FRICTION
                if abs(self.vel_x) < 0.4:
                    self.vel_x  = 0.0
                    self.active = False
                    settled     = True

        # ── Left wall ──
        if self._pos_x < 0:
            self._pos_x = 0.0
            self.vel_x  = abs(self.vel_x) * WALL_BOUNCE

        # ── Right wall ──
        elif self._pos_x + win_w > screen_w:
            self._pos_x = float(screen_w - win_w)
            self.vel_x  = -abs(self.vel_x) * WALL_BOUNCE

        return int(round(self._pos_x)), int(round(self._pos_y)), bounced, settled
