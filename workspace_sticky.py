"""
workspace_sticky.py - Make a window visible on every virtual desktop/workspace.

Linux/X11 only. This is exactly what GTK's Gtk.Window.stick() did under
the hood in the old version of this app — there's no Qt equivalent, so
this reimplements the same EWMH mechanism directly via python-xlib.

Why this can't be "fixed" everywhere:
  - X11: works, via the _NET_WM_STATE_STICKY EWMH hint below. Most
    window managers (GNOME, KDE, XFCE, i3, etc. when running X11
    sessions) respect this.
  - Wayland: not possible. Wayland's security model deliberately keeps
    clients from knowing about or controlling workspace assignment —
    only the compositor decides this, and there's no stable
    cross-compositor protocol exposing it. GTK's stick() didn't work
    under Wayland either, for the same reason — this isn't a regression
    from the old version, it's a pre-existing platform limit.
  - Windows/macOS: "workspace" isn't quite the same concept (Virtual
    Desktops / Spaces), and neither OS exposes a per-window "show on
    all of them" flag to applications the way X11's EWMH does.

So: call make_sticky(widget) after showing a window. It does the right
thing on X11 and silently no-ops everywhere else.
"""

import sys


def _on_x11() -> bool:
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.platformName() == 'xcb'
    except Exception:
        return False


def make_sticky(widget) -> bool:
    """
    Marks `widget` (a QWidget that's already been shown — needs a real
    native window ID) as visible on all workspaces. Returns True if it
    actually did something, False if this platform/session doesn't
    support it (caller can ignore the return value; this is best-effort).
    """
    if sys.platform.startswith('win') or sys.platform == 'darwin':
        return False
    if not _on_x11():
        return False  # Wayland or unknown — see module docstring

    try:
        from Xlib import display, X
        from Xlib.protocol import event

        win_id = int(widget.winId())
        d = display.Display()
        root = d.screen().root
        window = d.create_resource_object('window', win_id)

        net_wm_state = d.intern_atom('_NET_WM_STATE')
        sticky       = d.intern_atom('_NET_WM_STATE_STICKY')

        # Per the EWMH spec, clients request this via a ClientMessage to
        # the root window rather than setting the property directly —
        # that's what lets the window manager actually act on it instead
        # of just seeing a property change it ignores.
        ev = event.ClientMessage(
            window=window,
            client_type=net_wm_state,
            data=(32, [1, sticky, 0, 1, 0]),  # 1 = _NET_WM_STATE_ADD
        )
        mask = X.SubstructureNotifyMask | X.SubstructureRedirectMask
        root.send_event(ev, event_mask=mask)
        d.flush()
        return True
    except Exception as e:
        print(f'[workspace_sticky] could not set sticky state: {e}')
        return False
