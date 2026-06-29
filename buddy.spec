# buddy.spec — PyInstaller build spec for Buddy.
#
# Build with:
#   pyinstaller buddy.spec
#
# Produces a standalone onedir bundle (dist/Buddy/) containing the
# executable plus all dependencies and the resources/ folder. Onedir
# (not onefile) is deliberate: onefile re-extracts everything to a fresh
# temp dir on every single launch, which is slow for a "starts every
# login" app — onedir extracts once at build time, so every launch after
# that is fast.
#
# Platform note: this spec produces whatever OS it's run on (a Windows
# .exe needs to be built BY RUNNING this on Windows; same for macOS).
# See .github/workflows/build.yml for building all three automatically.

import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# QtMultimedia's FFmpeg backend plugin needs to be explicitly collected —
# PyInstaller's PySide6 hook doesn't always pull in every multimedia
# plugin automatically.
multimedia_datas = collect_data_files('PySide6', subdir='Qt/plugins/multimedia')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
    ] + multimedia_datas,
    hiddenimports=[
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Buddy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # no terminal window — set True temporarily if you need to see print() output while debugging a build
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/icon.ico',  # Windows .exe icon. Harmless no-op on Linux/macOS — see BUNDLE below for macOS.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Buddy',
)

# macOS: also produce a proper .app bundle (COLLECT alone gives a folder
# of files, not something Finder treats as a double-clickable app).
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Buddy.app',
        icon='icons/icon.icns',
        bundle_identifier='com.buddy.app',
        info_plist={
            'LSUIElement': True,  # no Dock icon / app-switcher entry — matches the "just a desktop pet" intent
            'CFBundleShortVersionString': '1.0.0',
        },
    )
