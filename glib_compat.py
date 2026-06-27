"""
glib_compat.py – Drop-in replacement for the two GLib functions the GTK
version relied on (timeout_add, idle_add), implemented on QTimer.

Why this exists: a huge amount of Buddy's actual behavior (mood drift,
economy evaluation, reminders, greeting logic, the adaptive tick loop)
is plain Python that happens to be scheduled via GLib.timeout_add(ms, cb)
— where cb returning True means "call me again", False/None means
"stop". Porting every one of those call sites to QTimer's different
API (connect a slot, .start()/.stop(), no return-value semantics) would
mean touching dozens of unrelated call sites and risking behavior
changes in logic that has nothing to do with the GTK-vs-Qt switch.

Instead: `from glib_compat import GLib` and use `GLib.timeout_add(...)`
and `GLib.idle_add(...)` exactly as before. Same semantics, Qt underneath.
"""

from PySide6.QtCore import QTimer

# Keeps active QTimer objects alive — PySide6 doesn't retain a Python
# reference to an unparented, started QTimer, so without this a timer
# can be garbage-collected mid-countdown even while still "running".
_active_timers = set()


class _GLibShim:
    @staticmethod
    def timeout_add(interval_ms, callback, *args):
        """Calls callback every interval_ms. callback returning a falsy
        value stops it; returning anything truthy keeps it going —
        same contract as GLib.timeout_add."""
        timer = QTimer()
        _active_timers.add(timer)

        def _fire():
            try:
                again = callback(*args)
            except Exception as e:
                print(f'[glib_compat] timeout callback error: {e}')
                again = False
            if not again:
                timer.stop()
                _active_timers.discard(timer)

        timer.timeout.connect(_fire)
        timer.start(int(interval_ms))
        return timer

    @staticmethod
    def idle_add(callback, *args):
        """Runs callback once, as soon as the event loop is free."""
        def _fire():
            try:
                callback(*args)
            except Exception as e:
                print(f'[glib_compat] idle callback error: {e}')
        QTimer.singleShot(0, _fire)
        return None

    @staticmethod
    def source_remove(timer) -> bool:
        """GLib.source_remove(id) equivalent — stop a timer returned by timeout_add."""
        if timer is None:
            return False
        try:
            timer.stop()
            _active_timers.discard(timer)
            return True
        except Exception:
            return False


GLib = _GLibShim()
