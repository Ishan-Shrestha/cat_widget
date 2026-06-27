"""
config.py – All constants and file-path definitions for Buddy.
"""

import os
import shutil
import sys

# ── Catppuccin Mocha palette ──
MAUVE   = (0.796, 0.651, 0.969)
RED     = (0.953, 0.545, 0.659)
GREEN   = (0.651, 0.890, 0.631)
YELLOW  = (0.976, 0.886, 0.686)
SKY     = (0.537, 0.863, 0.922)
TEAL    = (0.573, 0.886, 0.843)
BASE    = (0.118, 0.118, 0.180)
SURFACE = (0.153, 0.157, 0.220)
OVERLAY = (0.271, 0.278, 0.369)
TEXT    = (0.804, 0.839, 0.957)
SUBTEXT = (0.651, 0.678, 0.796)

PRIO_COLOR = {'high': RED, 'med': YELLOW, 'low': MAUVE}

# ── Tag palette (cycles through these for #tag pills) ──
TAG_COLORS = [SKY, TEAL, GREEN, MAUVE, YELLOW, RED]

# ── Recurring task recur values ──
# NOTE: RECUR_NONE is '' (empty string) intentionally — it's stored in JSON
# and indexed into combo boxes. Changing to None would require a data migration.
RECUR_NONE    = ''
RECUR_DAILY   = 'daily'
RECUR_WEEKLY  = 'weekly'
RECUR_MONTHLY = 'monthly'

# ── Base directory ──
# Derived from this file's own location so bundled assets are found no
# matter where the package is installed (site-packages, a venv, or a
# plain extracted folder) — EXCEPT when frozen by PyInstaller: bundled
# .py code gets packed into an opaque archive and __file__ no longer
# points anywhere useful on disk, while DATA files (resources/) get
# extracted to sys._MEIPASS instead (set in both --onefile and --onedir
# modes). So: check for that first, fall back to __file__ otherwise.
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = BASE_DIR   # read-only assets ship inside the package


def _user_data_dir(app_name: str) -> str:
    """
    Per-OS, per-user directory for *mutable* app data (todos, economy,
    skin choice, logs) — kept separate from RESOURCES_DIR because an
    installed package's own folder (e.g. site-packages) isn't a safe
    place to write to: it may not be writable, and gets wiped/replaced
    on every upgrade or reinstall.

      Linux:   $XDG_DATA_HOME or ~/.local/share/<app_name>   (hidden — dotdir)
      macOS:   ~/Library/Application Support/<app_name>
      Windows: %LOCALAPPDATA%\\<app_name>                    (hidden by default in Explorer)
    """
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path


DATA_DIR = _user_data_dir('Buddy')
_BASE = RESOURCES_DIR  # kept for readability below: assets live under _BASE


def _migrate_legacy_data() -> None:
    """
    One-time migration from the old layout (data/state folders sitting next
    to the code, from before this was a proper package) into DATA_DIR — so
    upgrading to the packaged version doesn't silently lose existing saves.
    Only copies known filenames, and only if the new location doesn't
    already have that file (never overwrites).
    """
    legacy_files = [
        ('data', 'data.json'), ('data', 'stats.json'), ('data', 'habits.json'),
        ('state', 'skin.json'), ('state', 'bond.json'),
        ('state', 'economy.json'), ('state', 'settings.json'),
    ]
    for subdir, name in legacy_files:
        src = os.path.join(_BASE, subdir, name)
        dst = os.path.join(DATA_DIR, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


_migrate_legacy_data()

# ── File paths ──
# Mutable, user-specific — lives in DATA_DIR (see _user_data_dir above).
DATA_FILE    = os.path.join(DATA_DIR, 'data.json')
STATS_FILE   = os.path.join(DATA_DIR, 'stats.json')
HABITS_FILE  = os.path.join(DATA_DIR, 'habits.json')
SKIN_FILE    = os.path.join(DATA_DIR, 'skin.json')
BOND_FILE    = os.path.join(DATA_DIR, 'bond.json')
ECONOMY_FILE = os.path.join(DATA_DIR, 'economy.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
LOG_FILE     = os.path.join(DATA_DIR, 'buddy.log')

# Bundled, read-only — ships inside the package/_BASE.
CHIME_FILE   = os.path.join(_BASE, 'resources', 'sounds', 'chime.wav')
MEOW_FILE    = os.path.join(_BASE, 'resources', 'sounds', 'meow.wav')
SUCCESS_FILE = os.path.join(_BASE, 'resources', 'sounds', 'success.wav')
POP_FILE     = os.path.join(_BASE, 'resources', 'sounds', 'pop.wav')
WHOOSH_FILE  = os.path.join(_BASE, 'resources', 'sounds', 'whoosh.wav')
THUD_FILE    = os.path.join(_BASE, 'resources', 'sounds', 'thud.wav')
PURR_FILE    = os.path.join(_BASE, 'resources', 'sounds', 'purr.wav')
SPRITE_DIR   = os.path.join(_BASE, 'resources', 'skins')
STAR_SPRITE  = os.path.join(_BASE, 'resources', 'fx', 'Star.png')
HEART_DIR = os.path.join(_BASE, 'resources', 'fx', 'heart_sheets')
HEART_ICON   = os.path.join(_BASE, 'resources', 'fx', 'heart.png')
ZZZ_SPRITE     = os.path.join(_BASE, 'resources', 'fx', 'zzz.png')
EXCLAIM_SPRITE = os.path.join(_BASE, 'resources', 'fx', 'exclaim.png')
SWEAT_SPRITE   = os.path.join(_BASE, 'resources', 'fx', 'sweat.png')
NOTE_SPRITE    = os.path.join(_BASE, 'resources', 'fx', 'note.png')

# ── Overlay sprite specs ──
ZZZ_FRAME_W     = 32
ZZZ_FRAMES      = 6
EXCLAIM_FRAME_W = 32
EXCLAIM_FRAMES  = 6
SWEAT_FRAME_W   = 31
SWEAT_FRAMES    = 6
NOTE_FRAME_W    = 31
NOTE_FRAMES     = 6
OVERLAY_FRAME_H = 32
GDRIVE_STATS = os.path.expanduser('~/GoogleDrive/Buddystats/buddy_stats.json')

# ── Economy ──
HEARTS_MAX           = 9
HEART_REPLENISH_COST = 50       # coins per heart
COIN_TODO_EASY       = 1
COIN_TODO_MED        = 2
COIN_TODO_HARD       = 4
COIN_POMO            = 3
COIN_HABIT           = 1
COIN_DAILY_BONUS     = 2
STREAK_MULTIPLIER    = 0.5      # extra x per 7-day streak week, caps at 2x

# ── Heart damage ──
DAMAGE_TODO_OVERDUE      = 0.5  # per overdue todo (high prio = x2)
DAMAGE_HABIT_MISS        = 0.25 # per missed habit
DAMAGE_STREAK_BROKEN     = 0.5  # extra on streak break

# ── Effect sprites ──
STAR_FRAME_SIZE  = 32
STAR_FRAMES      = 13

# ── Sprite / rendering ──
FRAME_SIZE   = 50
RENDER_SCALE = 4
AUTO_TRIM    = True
FIXED_CANVAS = True

# ── Timing (ms) ──
TICK_FAST        = 50
TICK_IDLE        = 180
POMO_TICK_MS     = 1000
REMIND_U_MS      = 60_000
REMIND_G_MS      = 180_000
BOND_SAVE_MS     = 5 * 60 * 1000
DEFERRED_LOAD_MS = 2000

# ── Habit reminder ──
HABIT_REMIND_WINDOW_START  = 6   # 06:00
HABIT_REMIND_WINDOW_END    = 21  # 21:00
HABIT_REMIND_JITTER_MIN    = 2   # minutes
HABIT_REMIND_JITTER_MAX    = 5   # minutes
HABIT_CHECKBOX_REMIND_HOUR = 21  # 21:00 single reminder

# ── Physics ──
GRAVITY       = 1.8       # strong gravity — proper parabolic arc, no floating
BOUNCE_COEF   = 0.42      # ~58% energy retained per bounce
FRICTION      = 0.80      # horizontal slowdown on floor contact
WALL_BOUNCE   = 0.30      # walls kill most horizontal momentum
DRAG_THRESH   = 4
THROW_SCALE   = 0.60      # throw feel
FLOOR_SLIDE   = 2
VEL_HISTORY   = 5         # frames of velocity history for throw calculation

# ── Panel / UI ──
PANEL_W  = 300
PANEL_H  = 440
BUBBLE_W = 200

# ── Animation definitions ──
PNG_ANIM_DEFS = {
    'idle':    ('Idle',       10),
    'walk':    ('Walk',        8),
    'run':     ('Run',         8),
    'sit':     ('Sitting',     1),
    'rest':    ('Laying',      8),
    'sleep1':  ('Sleeping1',   1),
    'sleep2':  ('Sleeping2',   1),
    'scratch': ('Stretching', 13),
    'itch':    ('Itch',        2),
    'lick':    ('Licking 1',   5),
    'lick2':   ('Licking 2',   5),
    'meow':    ('Meow',        4),
}

EAGER_ANIMS = frozenset({'idle', 'walk', 'sit', 'run'})

STATE_TO_ANIM = {
    'idle':       'idle', 'idle_tilt':  'idle', 'idle_lift':  'idle',
    'idle_yes':   'idle', 'idle_no':    'idle', 'idle_eat':   'lick',
    'sit':        'sit',  'sit_tilt':   'sit',  'sit_yes':    'sit',
    'sit_no':     'sit',  'sit_lift':   'sit',  'sit_eat':    'lick2',
    'walk':       'walk', 'walk_back':  'walk', 'run':        'run',
    'rest':       'rest', 'dream':      'sleep1',
    'scratch':    'scratch', 'itch':    'itch',
    'lick':       'lick',   'lick2':   'lick2', 'meow':      'meow',
    'stand_up':   'idle',   'sit_down': 'sit',  'spawn':     'idle',
    'dance':      'walk',   'focus':    'walk',  'sneak':     'sit',
    'sneak_move': 'walk',   'aggress':  'meow',  'attack':    'itch',
    'dig':        'scratch','poop':     'sit',   'pushes':    'scratch',
}

REST_STATES      = frozenset({'rest', 'dream', 'sleep1', 'sleep2'})
REST_HOLD_FRAMES = 2
