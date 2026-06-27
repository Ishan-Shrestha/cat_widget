"""
startup.py – Cross-platform "run at login" registration.

Replaces the old Linux-only approach (a hand-written ~/.config/autostart
.desktop file pointing at start_buddy.sh). That approach only worked
because the app was a folder of scripts living at a fixed, known path.
Once Buddy is an installed package — and especially once it's frozen
into a standalone app via PyInstaller — there's no guarantee of a
predictable script path, and there's no Linux-only assumption to lean on.

This module figures out the right command to relaunch Buddy (handling
both "running from source" and "frozen into an exe" cases) and registers
it with whichever OS-native autostart mechanism applies:

    Linux:   ~/.config/autostart/<APP_ID>.desktop   (XDG autostart)
    macOS:   ~/Library/LaunchAgents/<APP_ID>.plist   (LaunchAgent)
    Windows: HKCU\\...\\Run registry value             (winreg)

The actual on/off decision (and persisting the user's preference) lives
in settings.json via config.SETTINGS_FILE — this module only knows how
to make the OS agree with that preference. See cat_controller.py for
where it's wired up, and panel_manager.py's Settings tab for the toggle.
"""

import os
import sys

APP_ID   = 'buddy'
APP_NAME = 'Buddy'


def _is_frozen() -> bool:
    """True when running from a PyInstaller-frozen executable."""
    return bool(getattr(sys, 'frozen', False))


def _run_command() -> list:
    """
    Command line that relaunches Buddy the same way it's running now —
    works whether we're a frozen exe or a plain `python3 main.py`.
    """
    if _is_frozen():
        return [sys.executable]
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')
    return [sys.executable, main_py]


# ── Linux (XDG autostart) ──────────────────────────────────────────────

def _linux_desktop_path() -> str:
    autostart_dir = os.path.join(
        os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
        'autostart',
    )
    return os.path.join(autostart_dir, f'{APP_ID}.desktop')


def _linux_enable() -> None:
    path = _linux_desktop_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exec_line = ' '.join(_run_command())
    with open(path, 'w') as f:
        f.write(
            '[Desktop Entry]\n'
            'Type=Application\n'
            f'Name={APP_NAME}\n'
            f'Exec={exec_line}\n'
            'Hidden=false\n'
            'X-GNOME-Autostart-enabled=true\n'
        )


def _linux_disable() -> None:
    try:
        os.remove(_linux_desktop_path())
    except FileNotFoundError:
        pass


def _linux_is_enabled() -> bool:
    return os.path.exists(_linux_desktop_path())


# ── macOS (LaunchAgent) ─────────────────────────────────────────────────

def _macos_plist_path() -> str:
    return os.path.expanduser(f'~/Library/LaunchAgents/com.{APP_ID}.app.plist')


def _macos_enable() -> None:
    path = _macos_plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    args = _run_command()
    args_xml = '\n'.join(f'        <string>{a}</string>' for a in args)
    with open(path, 'w') as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            f'    <key>Label</key><string>com.{APP_ID}.app</string>\n'
            '    <key>ProgramArguments</key>\n'
            '    <array>\n'
            f'{args_xml}\n'
            '    </array>\n'
            '    <key>RunAtLoad</key><true/>\n'
            '</dict>\n'
            '</plist>\n'
        )
    os.system(f'launchctl load "{path}" >/dev/null 2>&1')


def _macos_disable() -> None:
    path = _macos_plist_path()
    os.system(f'launchctl unload "{path}" >/dev/null 2>&1')
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _macos_is_enabled() -> bool:
    return os.path.exists(_macos_plist_path())


# ── Windows (registry Run key) ──────────────────────────────────────────

_WIN_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def _windows_enable() -> None:
    import winreg
    cmd = ' '.join(f'"{a}"' for a in _run_command())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)


def _windows_disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def _windows_is_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False


# ── Public, platform-dispatching API ────────────────────────────────────

def is_enabled() -> bool:
    try:
        if sys.platform.startswith('win'):
            return _windows_is_enabled()
        if sys.platform == 'darwin':
            return _macos_is_enabled()
        return _linux_is_enabled()
    except Exception as e:
        print(f'[startup] is_enabled check failed: {e}')
        return False


def set_enabled(flag: bool) -> None:
    try:
        if sys.platform.startswith('win'):
            _windows_enable() if flag else _windows_disable()
        elif sys.platform == 'darwin':
            _macos_enable() if flag else _macos_disable()
        else:
            _linux_enable() if flag else _linux_disable()
    except Exception as e:
        print(f'[startup] set_enabled({flag}) failed: {e}')
