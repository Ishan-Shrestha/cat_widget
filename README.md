# Buddy

A pixel-art cat that lives on your desktop. Pet it, drag it around, throw it across the screen and use it as a pomodoro timer, todo list, and habit tracker while it's at it.

![cat skins](resources/skins/preview.png)

## What it does

- Sits on your screen, wanders around, reacts when you click or drag it
- Click it to open a panel with a pomodoro timer, todos, habits, and stats
- Earn coins for finishing tasks and habits, lose hearts for missing them
- 6 skins to pick from
- Starts automatically at login (toggle this off in Settings if you don't want it)
- Right-click for the menu, including quit

## Running it

```bash
pip install -r requirements.txt
python3 main.py
```

On Linux you'll also need `libxcb-cursor0` installed (`sudo apt install libxcb-cursor0`, or `xcb-util-cursor` on Fedora/Arch). It's a system package, pip can't install it for you.

`./start_buddy.sh` runs it in the background instead of tying up your terminal.

## Building a standalone app

```bash
pip install pyinstaller
pyinstaller buddy.spec
```

This bundles everything into `dist/Buddy/`, no Python install required on the machine you run it on.

Windows and macOS builds can't be made from Linux — PyInstaller has to run on the actual target OS. `.github/workflows/build.yml` handles this by building all three on GitHub's own runners whenever a `v*` tag is pushed, and drafts a release with the results.

## Status

Runs and has been tested on Linux. The Windows and macOS builds come from the same code and the same CI pipeline, but haven't been run on real Windows/macOS hardware yet.

## Credits

Cat sprites: Pet Cats Pack (CC0) — see `resources/skins/License.txt`.
