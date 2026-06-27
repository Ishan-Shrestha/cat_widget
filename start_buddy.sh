#!/bin/bash
# Convenience launcher - runs Buddy detached from the terminal, with its
# own log file, instead of occupying the foreground shell.
#
# This is a stop-gap for "running from source" on Linux/macOS. The real,
# permanent fix is the planned PyInstaller-bundled executable: launched
# by double-clicking an icon (or via the autostart entry startup.py
# already sets up), there's no terminal involved at all, so this script
# becomes unnecessary at that point.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/Buddy"
mkdir -p "$DATA_DIR"
nohup python3 "$DIR/main.py" > "$DATA_DIR/buddy.log" 2>&1 &
disown
echo "Buddy started in the background (PID $!). Log: $DATA_DIR/buddy.log"
echo "Quit it the normal way: right-click the cat -> Quit Buddy."
