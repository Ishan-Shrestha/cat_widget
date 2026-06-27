"""
effects.py - Floating star/heart effect particles drawn on the cat's own window.

Ported from the GTK version. Same approach throughout this package: all
the particle math (lifetimes, alpha fades, heart-splitting logic for
multi-heart damage/heal) is unchanged. What changed:

  - GdkPixbuf.Pixbuf  -> QPixmap (via _pil_to_pixmap, same pattern as
    sprite_manager.py)
  - Gdk.cairo_set_source_pixbuf(cr, pb, x, y) + cr.paint_with_alpha(a)
    -> painter.setOpacity(a); painter.drawPixmap(x, y, pb)
  - cr.show_text(...)  -> painter.drawText(...)
  - pb.get_width()/get_height() -> pb.width()/height() (QPixmap has no
    get_ prefix)

Stars animate through all frames (coin reward).
Hearts animate from current state to damaged/healed state, chained for
multi-heart damage.
"""

import os
from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPixmap, QImage, QFont, QColor

from config import (
    STAR_SPRITE, HEART_DIR,
    STAR_FRAME_SIZE, STAR_FRAMES,
    HEARTS_MAX,
    GREEN, RED, YELLOW,
)


def _qc(rgb_tuple, alpha=1.0) -> QColor:
    r, g, b = rgb_tuple
    return QColor(int(r * 255), int(g * 255), int(b * 255), int(alpha * 255))


# ── Sprite loading ────────────────────────────────────────────────────────

def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    img  = img.convert('RGBA')
    data = img.tobytes('raw', 'RGBA')
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def _load_frames(path, fw, fh, n, scale=1):
    if not os.path.exists(path):
        print(f'[effects] sprite not found: {path}')
        return []
    try:
        with Image.open(path) as sheet:
            sheet  = sheet.convert('RGBA')
            frames = []
            for i in range(n):
                frame = sheet.crop((i * fw, 0, (i + 1) * fw, fh))
                if scale != 1:
                    frame = frame.resize((fw * scale, fh * scale), Image.NEAREST)
                frames.append(_pil_to_pixmap(frame))
            print(f'[effects] loaded {len(frames)} frames from {os.path.basename(path)}')
            return frames
    except Exception as e:
        print(f'[effects] Failed to load {path}: {e}')
        return []


def _load_heart_states(folder):
    states = {}
    for state in [100, 75, 50, 25, 0]:
        path = os.path.join(folder, f'{state}.png')
        if not os.path.exists(path):
            print(f'[effects] missing heart: {path}')
            continue
        try:
            img = Image.open(path).convert('RGBA')
            states[state] = _pil_to_pixmap(img)
        except Exception as e:
            print(f'[effects] failed loading {path}: {e}')
    return states


class _Sprites:
    _instance = None

    def __init__(self):
        self.star  = _load_frames(STAR_SPRITE, STAR_FRAME_SIZE, STAR_FRAME_SIZE, STAR_FRAMES, 1)
        self.heart = _load_heart_states(HEART_DIR)

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = _Sprites()
        return cls._instance


# ── Heart frame helpers ───────────────────────────────────────────────────

def _heart_fraction_to_frame(value: float) -> int:
    value = max(0.0, min(1.0, value))
    if value >= 1.0:   return 100
    elif value >= 0.75: return 75
    elif value >= 0.5:  return 50
    elif value > 0.0:   return 25
    return 0


def _split_hearts(total: float, max_hearts: int = HEARTS_MAX):
    """Converts total health into per-heart fill values."""
    hearts = []
    for i in range(max_hearts):
        value = max(0.0, min(1.0, total - i))
        hearts.append(round(value, 2))
    return hearts


# ── Particles ─────────────────────────────────────────────────────────────

class _StarParticle:
    """Coin reward - animates through star frames while floating up."""
    LIFETIME = 55
    TICKS_PER_FRAME = 3

    def __init__(self, amount: int, x: float, y: float):
        self.amount = amount
        self.x      = x
        self.y      = y
        self.tick   = 0
        self.frame  = 0
        self._ft    = 0

    @property
    def alive(self): return self.tick < self.LIFETIME

    @property
    def alpha(self):
        return min(1.0, self.tick / 6) * min(1.0, (self.LIFETIME - self.tick) / 10)

    def advance(self):
        self.tick += 1
        self._ft  += 1
        if self._ft >= self.TICKS_PER_FRAME:
            self._ft   = 0
            self.frame += 1

    def draw(self, p: QPainter, sprites):
        alpha = self.alpha
        if alpha <= 0 or not sprites.star:
            return
        rise = self.tick * 0.7
        x, y = self.x, self.y - rise
        pm   = sprites.star[self.frame % len(sprites.star)]
        fw   = pm.width()
        fh   = pm.height()
        p.save()
        p.setOpacity(alpha)
        p.drawPixmap(int(x - fw // 2), int(y - fh // 2), pm)
        p.setOpacity(1.0)
        label = f'+{self.amount}'
        p.setFont(QFont('Sans', 9, QFont.Bold))
        p.setPen(_qc((0, 0, 0), alpha * 0.5))
        p.drawText(QPointF(x + fw // 2 + 2, y + 3), label)
        p.setPen(_qc(YELLOW, alpha))
        p.drawText(QPointF(x + fw // 2 + 1, y + 2), label)
        p.restore()


class _FloatingTextParticle:
    LIFETIME = 40

    def __init__(self, text, color, x, y, delay=0):
        self.text  = text
        self.color = color
        self.x     = x
        self.y     = y
        self.tick  = 0
        self.delay = delay

    @property
    def alive(self):
        return self.tick < self.LIFETIME

    @property
    def alpha(self):
        return max(0.0, 1.0 - (self.tick / self.LIFETIME))

    def advance(self):
        if self.delay > 0:
            self.delay -= 1
            return
        self.tick += 1

    def draw(self, p: QPainter, sprites):
        if self.delay > 0:
            return
        alpha = self.alpha
        y = self.y - (self.tick * 0.5)
        p.save()
        p.setFont(QFont('Sans', 14, QFont.Bold))
        p.setPen(_qc((0, 0, 0), alpha * 0.5))
        p.drawText(QPointF(self.x + 1, y + 1), self.text)
        p.setPen(_qc(self.color, alpha))
        p.drawText(QPointF(self.x, y), self.text)
        p.restore()


class _HeartParticle:
    """
    Heart damage/heal - animates frame-by-frame from start_frame to end_frame.
    For damage: start=full(0) -> end=damaged state, slowly.
    For heal:   start=current -> end=full(0), reversed.
    TICKS_PER_FRAME controls how slowly the animation plays.
    """
    TICKS_PER_FRAME = 8
    HOLD_TICKS      = 12
    FADE_TICKS      = 12

    def __init__(self, start_frame, end_frame, kind, amount, x, y, delay=0):
        self.start_frame = start_frame
        self.end_frame   = end_frame
        self.kind        = kind
        self.amount      = amount
        self.x           = x
        self.y           = y
        self.tick        = 0
        self._ft         = 0
        self.delay       = delay

        states = [100, 75, 50, 25, 0]
        start_idx = states.index(start_frame)
        end_idx   = states.index(end_frame)
        if start_idx <= end_idx:
            self._frames = states[start_idx:end_idx + 1]
        else:
            self._frames = list(reversed(states[end_idx:start_idx + 1]))
        self._fi        = 0
        self._animating = True
        self._hold      = 0

        total_anim = len(self._frames) * self.TICKS_PER_FRAME
        self.LIFETIME = total_anim + self.HOLD_TICKS + self.FADE_TICKS

    @property
    def alive(self): return self.tick < self.LIFETIME

    @property
    def current_frame(self): return self._frames[min(self._fi, len(self._frames) - 1)]

    @property
    def alpha(self):
        total_anim = len(self._frames) * self.TICKS_PER_FRAME
        hold_end   = total_anim + self.HOLD_TICKS
        if self.tick <= total_anim + self.HOLD_TICKS:
            return min(1.0, self.tick / 5)
        fade_progress = (self.tick - hold_end) / self.FADE_TICKS
        return max(0.0, 1.0 - fade_progress)

    def advance(self):
        if self.delay > 0:
            self.delay -= 1
            return
        self.tick += 1
        self._ft  += 1
        if self._animating and self._ft >= self.TICKS_PER_FRAME:
            self._ft  = 0
            self._fi += 1
            if self._fi >= len(self._frames):
                self._fi        = len(self._frames) - 1
                self._animating = False

    def draw(self, p: QPainter, sprites):
        if self.delay > 0:
            return
        alpha = self.alpha
        if alpha <= 0 or not sprites.heart:
            return
        fi = self.current_frame
        pm = sprites.heart.get(fi)
        if pm is None:
            return
        fw, fh = pm.width(), pm.height()
        p.save()
        p.setOpacity(alpha)
        p.drawPixmap(int(self.x - fw // 2), int(self.y - fh // 2), pm)
        p.setOpacity(1.0)
        p.restore()


# ── Public API ────────────────────────────────────────────────────────────

class EffectsOverlay:
    """
    No separate window. Particles drawn directly on the cat's widget.
    Call spawn*() to add particles, tick() each fast tick,
    draw(painter) inside the cat's paintEvent to render them.
    """

    def __init__(self):
        self._particles = []
        self._sprites   = None

    def _ensure_sprites(self):
        if self._sprites is None:
            self._sprites = _Sprites.get()

    def spawn(self, kind: str, amount: float,
              wx: int, wy: int, wh: int, win_w: int = 200, **kwargs) -> None:
        """Main spawn entry point. Dispatches to correct particle type."""
        self._ensure_sprites()
        cx = win_w // 2
        cy = wh // 2

        if kind == 'coins':
            self._particles.append(_StarParticle(int(amount), cx, cy))
        elif kind == 'damage':
            hearts_after  = kwargs.get('hearts_remaining', HEARTS_MAX)
            hearts_before = hearts_after + amount
            self._spawn_heart_damage(hearts_before, hearts_after, cx, cy)
        elif kind == 'heal':
            hearts_after  = kwargs.get('hearts_remaining', HEARTS_MAX)
            hearts_before = hearts_after - amount
            self._spawn_heart_heal(hearts_before, hearts_after, cx, cy)

    def _spawn_heart_damage(self, hearts_before, hearts_after, cx, cy) -> None:
        damage = hearts_before - hearts_after
        hearts = _split_hearts(hearts_before)
        damage_remaining = damage
        affected = []

        for i in reversed(range(len(hearts))):
            if damage_remaining <= 0:
                break
            current_fill = hearts[i]
            if current_fill <= 0:
                continue
            consume = min(current_fill, damage_remaining)
            affected.append((current_fill, current_fill - consume))
            damage_remaining -= consume

        for idx, (start_fill, end_fill) in enumerate(affected):
            start_f = _heart_fraction_to_frame(start_fill)
            end_f   = _heart_fraction_to_frame(end_fill)
            self._particles.append(
                _HeartParticle(start_f, end_f, 'damage', damage, cx, cy, delay=idx * 30))

        total_delay = len(affected) * 30
        self._particles.append(
            _FloatingTextParticle(f'-{damage}', RED, cx, cy - 24, delay=total_delay))

    def _spawn_heart_heal(self, hearts_before, hearts_after, cx, cy) -> None:
        heal = hearts_after - hearts_before
        hearts = _split_hearts(hearts_before)
        heal_remaining = heal
        affected = []

        for i in range(len(hearts)):
            if heal_remaining <= 0:
                break
            current_fill = hearts[i]
            if current_fill >= 1.0:
                continue
            restore = min(1.0 - current_fill, heal_remaining)
            affected.append((current_fill, current_fill + restore))
            heal_remaining -= restore

        for idx, (start_fill, end_fill) in enumerate(affected):
            start_f = _heart_fraction_to_frame(start_fill)
            end_f   = _heart_fraction_to_frame(end_fill)
            self._particles.append(
                _HeartParticle(start_f, end_f, 'heal', heal, cx, cy, delay=idx * 30))

        total_delay = len(affected) * 30
        self._particles.append(
            _FloatingTextParticle(f'+{heal}', GREEN, cx, cy - 24, delay=total_delay))

    def tick(self) -> bool:
        if not self._particles:
            return False
        for p in self._particles:
            p.advance()
        self._particles = [p for p in self._particles if p.alive]
        return True

    def draw(self, p: QPainter) -> None:
        if not self._particles or self._sprites is None:
            return
        for particle in self._particles:
            particle.draw(p, self._sprites)

    @property
    def active(self):
        return bool(self._particles)


# ── Overlay sprite particles ──────────────────────────────────────────────

def _load_overlay_frames(path, fw, fh, n, scale=2):
    """Load overlay sprite frames same as star/heart loader."""
    if not os.path.exists(path):
        print(f'[effects] overlay sprite not found: {path}')
        return []
    try:
        with Image.open(path) as sheet:
            sheet  = sheet.convert('RGBA')
            frames = []
            for i in range(n):
                frame = sheet.crop((i * fw, 0, (i + 1) * fw, fh))
                if scale != 1:
                    frame = frame.resize((fw * scale, fh * scale), Image.NEAREST)
                frames.append(_pil_to_pixmap(frame))
            return frames
    except Exception as e:
        print(f'[effects] overlay load failed {path}: {e}')
        return []


class _OverlaySprites:
    _instance = None

    def __init__(self):
        from config import (
            ZZZ_SPRITE, ZZZ_FRAME_W, ZZZ_FRAMES,
            EXCLAIM_SPRITE, EXCLAIM_FRAME_W, EXCLAIM_FRAMES,
            SWEAT_SPRITE, SWEAT_FRAME_W, SWEAT_FRAMES,
            NOTE_SPRITE, NOTE_FRAME_W, NOTE_FRAMES,
            OVERLAY_FRAME_H,
        )
        self.zzz     = _load_overlay_frames(ZZZ_SPRITE,     ZZZ_FRAME_W,     OVERLAY_FRAME_H, ZZZ_FRAMES,     scale=2)
        self.exclaim = _load_overlay_frames(EXCLAIM_SPRITE, EXCLAIM_FRAME_W, OVERLAY_FRAME_H, EXCLAIM_FRAMES, scale=2)
        self.sweat   = _load_overlay_frames(SWEAT_SPRITE,   SWEAT_FRAME_W,   OVERLAY_FRAME_H, SWEAT_FRAMES,   scale=2)
        self.note    = _load_overlay_frames(NOTE_SPRITE,    NOTE_FRAME_W,    OVERLAY_FRAME_H, NOTE_FRAMES,    scale=2)
        print(f'[effects] overlay sprites loaded: zzz={len(self.zzz)} exclaim={len(self.exclaim)} sweat={len(self.sweat)} note={len(self.note)}')

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = _OverlaySprites()
        return cls._instance


class _OverlayParticle:
    """
    Sprite-based overlay particle - plays through frames then fades.
    Floats upward slightly while animating.
    """
    TICKS_PER_FRAME = 6
    HOLD_TICKS      = 10
    FADE_TICKS      = 12

    def __init__(self, kind: str, x: float, y: float, delay: int = 0):
        self.kind  = kind
        self.x     = x
        self.y     = y
        self.delay = delay
        self.tick  = 0
        self._ft   = 0
        self._fi   = 0
        self._done = False

    def _frames_for(self, sprites: _OverlaySprites) -> list:
        return getattr(sprites, self.kind, [])

    @property
    def _active_tick(self) -> int:
        return max(0, self.tick - self.delay)

    def alive(self, sprites: _OverlaySprites) -> bool:
        n     = len(self._frames_for(sprites))
        total = max(1, n) * self.TICKS_PER_FRAME + self.HOLD_TICKS + self.FADE_TICKS + self.delay
        return self.tick < total

    @property
    def alpha(self) -> float:
        t = self._active_tick
        if t == 0:
            return 0.0
        return min(1.0, t / 6) * (1.0 if not self._done else
               max(0.0, 1.0 - (t - self._anim_end - self.HOLD_TICKS) / self.FADE_TICKS))

    def advance(self, sprites: _OverlaySprites) -> None:
        self.tick += 1
        if self.tick <= self.delay:
            return
        frames = self._frames_for(sprites)
        n = len(frames)
        if n == 0:
            return
        self._anim_end = n * self.TICKS_PER_FRAME
        if not self._done:
            self._ft += 1
            if self._ft >= self.TICKS_PER_FRAME:
                self._ft  = 0
                self._fi += 1
                if self._fi >= n:
                    self._fi   = n - 1
                    self._done = True

    def draw(self, p: QPainter, sprites: _OverlaySprites) -> None:
        if self.tick <= self.delay:
            return
        frames = self._frames_for(sprites)
        if not frames:
            return
        t      = self._active_tick
        n      = len(frames)
        anim_e = n * self.TICKS_PER_FRAME
        if t < anim_e + self.HOLD_TICKS:
            alpha = min(1.0, t / 6)
        else:
            fade_t = t - anim_e - self.HOLD_TICKS
            alpha  = max(0.0, 1.0 - fade_t / self.FADE_TICKS)

        if alpha <= 0:
            return

        rise = self._active_tick * 0.4
        pm   = frames[min(self._fi, len(frames) - 1)]
        fw, fh = pm.width(), pm.height()
        p.save()
        p.setOpacity(alpha)
        p.drawPixmap(int(self.x - fw // 2), int(self.y - fh // 2 - rise), pm)
        p.setOpacity(1.0)
        p.restore()


# ── Extend EffectsOverlay with overlay spawn ──────────────────────────────

_orig_spawn = EffectsOverlay.spawn


def _spawn_extended(self, kind: str, amount: float,
                    wx: int, wy: int, wh: int, win_w: int = 200, **kwargs) -> None:
    cx = win_w // 2
    cy = wh // 2

    overlay_kinds = ('zzz', 'exclaim', 'sweat', 'note')
    if kind in overlay_kinds:
        if not hasattr(self, '_overlay_sprites') or self._overlay_sprites is None:
            self._overlay_sprites = _OverlaySprites.get()
        if kind == 'zzz':
            for i in range(3):
                self._particles.append(_OverlayParticle('zzz', cx + i * 6, cy - i * 8, delay=i * 20))
        elif kind == 'exclaim':
            self._particles.append(_OverlayParticle('exclaim', cx, cy - 20))
        elif kind == 'sweat':
            for i in range(2):
                self._particles.append(_OverlayParticle('sweat', cx + i * 14 - 7, cy - 10, delay=i * 15))
        elif kind == 'note':
            for i in range(2):
                self._particles.append(_OverlayParticle('note', cx + i * 16 - 8, cy - 8, delay=i * 18))
    else:
        _orig_spawn(self, kind, amount, wx, wy, wh, win_w, **kwargs)


EffectsOverlay.spawn = _spawn_extended
EffectsOverlay._overlay_sprites = None


# Patch tick and draw to handle _OverlayParticle alive check
_orig_tick = EffectsOverlay.tick
_orig_draw = EffectsOverlay.draw


def _tick_extended(self) -> bool:
    if not self._particles:
        return False
    os_ = getattr(self, '_overlay_sprites', None)
    for p in self._particles:
        if isinstance(p, _OverlayParticle):
            p.advance(os_)
        else:
            p.advance()
    self._particles = [
        p for p in self._particles if
        (p.alive(os_) if isinstance(p, _OverlayParticle) else p.alive)
    ]
    return True


def _draw_extended(self, p: QPainter) -> None:
    if not self._particles:
        return
    os_ = getattr(self, '_overlay_sprites', None)
    for particle in self._particles:
        if isinstance(particle, _OverlayParticle) and os_ is not None:
            particle.draw(p, os_)
        elif not isinstance(particle, _OverlayParticle) and self._sprites is not None:
            particle.draw(p, self._sprites)


EffectsOverlay.tick = _tick_extended
EffectsOverlay.draw = _draw_extended
