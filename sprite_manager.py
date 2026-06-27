"""
sprite_manager.py – Loads and caches sprite frames from the skins directory.

Ported from the GTK version: the only actual change is _pil_to_pixbuf ->
_pil_to_pixmap (GdkPixbuf.Pixbuf -> QPixmap). Everything else — frame
extraction, trimming, canvas placement, the public SpriteManager API —
is unchanged, since none of it touched GTK directly.
"""

import os
from PIL import Image
from PySide6.QtGui import QImage, QPixmap

from config import (
    SPRITE_DIR, FRAME_SIZE, RENDER_SCALE, AUTO_TRIM, FIXED_CANVAS,
    PNG_ANIM_DEFS, EAGER_ANIMS, STATE_TO_ANIM
)


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    img  = img.convert('RGBA')
    data = img.tobytes('raw', 'RGBA')
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    # .copy() forces a deep copy — `data` is a local bytes object that would
    # otherwise be freed once this function returns, leaving QImage pointing
    # at freed memory (QImage doesn't copy the buffer it's constructed from).
    return QPixmap.fromImage(qimg.copy())


def _trim_image(img: Image.Image) -> Image.Image:
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _place_on_canvas(img: Image.Image, size: tuple) -> Image.Image:
    canvas = Image.new('RGBA', size, (0, 0, 0, 0))
    x = (size[0] - img.width) // 2
    y = size[1] - img.height
    canvas.paste(img, (x, y), img)
    return canvas


def _extract_frames(sheet: Image.Image, frame_size: int, n_frames=None) -> list:
    total = sheet.width // frame_size
    if n_frames:
        total = min(total, n_frames)
    frames = []
    for i in range(total):
        frame = sheet.crop((i * frame_size, 0, (i + 1) * frame_size, sheet.height))
        if AUTO_TRIM:
            frame = _trim_image(frame)
        if FIXED_CANVAS:
            frame = _place_on_canvas(frame, (frame_size, frame_size))
        frame = frame.resize(
            (frame.width * RENDER_SCALE, frame.height * RENDER_SCALE),
            Image.NEAREST
        )
        frames.append(_pil_to_pixmap(frame))
    return frames


class SpriteManager:
    """Loads and caches sprite frames for all animations."""

    def __init__(self):
        self.anims: dict      = {}
        self.sprite_size: int = FRAME_SIZE * RENDER_SCALE

    def load_skin(self, skin_number: int) -> bool:
        cat_dir = os.path.join(SPRITE_DIR, f'Cat-{skin_number}')
        if not os.path.isdir(cat_dir):
            print(f'[buddy] Sprite folder not found: {cat_dir}')
            return False

        anims = {}
        for key in EAGER_ANIMS:
            frames = self._load_anim(cat_dir, skin_number, key)
            if frames:
                anims[key] = frames

        if not anims:
            return False

        if 'idle' not in anims:
            anims['idle'] = next(iter(anims.values()))

        self.anims = anims
        return True

    def load_deferred(self, skin_number: int) -> None:
        cat_dir = os.path.join(SPRITE_DIR, f'Cat-{skin_number}')
        if not os.path.isdir(cat_dir):
            return
        for key in PNG_ANIM_DEFS:
            if key in EAGER_ANIMS or key in self.anims:
                continue
            frames = self._load_anim(cat_dir, skin_number, key)
            if frames:
                self.anims[key] = frames

    def _load_anim(self, cat_dir: str, skin_number: int, key: str) -> list:
        filename, n_frames = PNG_ANIM_DEFS[key]
        path = os.path.join(cat_dir, f'Cat-{skin_number}-{filename}.png')
        if not os.path.exists(path):
            return []
        try:
            with Image.open(path) as sheet:
                sheet = sheet.convert('RGBA')
                return _extract_frames(sheet, FRAME_SIZE, n_frames)
        except Exception as e:
            print(f'[buddy] Error loading {path}: {e}')
            return []

    def resolve(self, state_key: str) -> list:
        anim_key = STATE_TO_ANIM.get(state_key, 'idle')
        frames   = self.anims.get(anim_key)
        if not frames:
            frames = self.anims.get('idle', [])
        return frames
