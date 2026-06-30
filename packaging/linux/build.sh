#!/bin/bash
# Builds both a .deb and an AppImage from dist/Buddy/ (the PyInstaller
# output). Run this AFTER `pyinstaller buddy.spec`, from the buddy_qt
# root or anywhere — it figures out its own location.
#
# Usage: packaging/linux/build.sh <version>
#   version: e.g. 0.1.0 (no leading "v" — strip that from the git tag
#   before passing it in)

set -e

VERSION="${1:-0.0.0}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ ! -d "dist/Buddy" ]; then
    echo "dist/Buddy not found — run 'pyinstaller buddy.spec' first." >&2
    exit 1
fi

ICON_SIZES="16 32 48 64 128 256 512"

# ── .deb ──
echo "Building .deb..."
DEB_ROOT="$(mktemp -d)"
mkdir -p "$DEB_ROOT/DEBIAN"
mkdir -p "$DEB_ROOT/usr/lib/buddy"
mkdir -p "$DEB_ROOT/usr/bin"
mkdir -p "$DEB_ROOT/usr/share/applications"
for size in $ICON_SIZES; do
    mkdir -p "$DEB_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
done

cp -r dist/Buddy/* "$DEB_ROOT/usr/lib/buddy/"
cp packaging/linux/deb/buddy-launcher.sh "$DEB_ROOT/usr/bin/buddy"
chmod +x "$DEB_ROOT/usr/bin/buddy"
cp packaging/linux/buddy.desktop "$DEB_ROOT/usr/share/applications/buddy.desktop"
for size in $ICON_SIZES; do
    cp "icons/linux/icon_${size}.png" "$DEB_ROOT/usr/share/icons/hicolor/${size}x${size}/apps/buddy.png"
done

sed "s/VERSION_PLACEHOLDER/${VERSION}/" packaging/linux/deb/control.template > "$DEB_ROOT/DEBIAN/control"

dpkg-deb --build --root-owner-group "$DEB_ROOT" "Buddy-${VERSION}-amd64.deb"
rm -rf "$DEB_ROOT"
echo "Built Buddy-${VERSION}-amd64.deb"

# ── AppImage ──
echo "Building AppImage..."
APPDIR="$(mktemp -d)/Buddy.AppDir"
mkdir -p "$APPDIR/usr/lib/buddy"
cp -r dist/Buddy/* "$APPDIR/usr/lib/buddy/"
cp packaging/linux/appimage/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/linux/buddy.desktop "$APPDIR/buddy.desktop"
cp icons/linux/icon_256.png "$APPDIR/buddy.png"

if [ ! -f appimagetool.AppImage ]; then
    wget -q https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage -O appimagetool.AppImage
    chmod +x appimagetool.AppImage
fi

ARCH=x86_64 ./appimagetool.AppImage "$APPDIR" "Buddy-${VERSION}-x86_64.AppImage" --no-appstream
echo "Built Buddy-${VERSION}-x86_64.AppImage"
