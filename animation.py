"""
animation.py – Tracks animation frame, speed, one-shot transitions, and rest loops.
"""

from config import REST_STATES, REST_HOLD_FRAMES
from sprite_manager import SpriteManager
from utils import _play_meow


class AnimationController:
    """Tracks animation frame, speed, one-shot transitions, and rest loops."""

    def __init__(self, sprites: SpriteManager):
        self._sprites          = sprites
        self.state             = 'idle'
        self.frame             = 0
        self.tick              = 0
        self.speed             = 6
        self.flip              = False
        self._transition_state = None
        self._transition_next  = None
        self._one_shot_done    = False
        self._rest_looped      = False

    @property
    def active_state(self) -> str:
        return self._transition_state if self._transition_state else self.state

    def set_state(self, new_state: str) -> None:
        if new_state == self.state and not self._transition_state:
            return
        self._rest_looped      = False
        self._transition_state = None
        self._transition_next  = None
        self._one_shot_done    = False
        self.state = new_state
        self.frame = 0
        self.tick  = 0

    def play_transition(self, one_shot: str, next_state: str) -> None:
        if one_shot == 'meow':
            _play_meow()
        self._transition_state = one_shot
        self._transition_next  = next_state
        self._one_shot_done    = False
        self.frame = 0
        self.tick  = 0

    def advance(self) -> bool:
        """Advance one tick. Returns True if the visible frame changed."""
        self.tick += 1
        if self.tick < self.speed:
            return False
        self.tick = 0

        active = self.active_state
        frames = self._sprites.resolve(active)
        n      = len(frames)
        if n == 0:
            return False

        if not self._transition_state and active in REST_STATES and n > REST_HOLD_FRAMES:
            if not self._rest_looped:
                self.frame += 1
                if self.frame >= n:
                    self._rest_looped = True
                    self.frame = n - REST_HOLD_FRAMES
            else:
                hold_start = n - REST_HOLD_FRAMES
                self.frame += 1
                if self.frame >= n:
                    self.frame = hold_start
        else:
            self.frame += 1

        if self._transition_state and self.frame > 0:
            if (self.frame % n) == 0:
                self._one_shot_done = True

        if self._transition_state and self._one_shot_done:
            self._transition_state = None
            self._one_shot_done    = False
            if self._transition_next:
                self.set_state(self._transition_next)
                self._transition_next = None

        return True

    def current_pixbuf(self):
        frames = self._sprites.resolve(self.active_state)
        if not frames:
            return None
        return frames[self.frame % len(frames)]
