#!/bin/sh
# PyInstaller's onedir build needs to stay next to its _internal/ folder,
# so this can't be a plain symlink from /usr/bin straight to the binary —
# it has to actually exec the real path inside /usr/lib/buddy.
exec /usr/lib/buddy/Buddy "$@"
