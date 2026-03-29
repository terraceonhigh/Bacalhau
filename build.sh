#!/bin/bash
# Build Bacalhau native packages.
# Produces: build/Bacalhau.app (macOS) and build/Bacalhau.AppDir (Linux)
set -euo pipefail

VERSION="${1:-dev}"
DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build"
ICON="$DIR/icon.png"

# Clean
rm -rf "$BUILD"
mkdir -p "$BUILD"

# Validate
if [ ! -f "$ICON" ]; then
    echo "Error: icon.png not found in repo root." >&2
    exit 1
fi
if [ ! -f "$DIR/editor.py" ]; then
    echo "Error: editor.py not found." >&2
    exit 1
fi

# ── macOS .app bundle ──────────────────────────────────────────────────────
echo "==> Building Bacalhau.app ($VERSION)..."

APP="$BUILD/Bacalhau.app"
CONTENTS="$APP/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# Icon: PNG → iconset → icns
ICONSET="$BUILD/icon.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
    sips -z $SIZE $SIZE "$ICON" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null 2>&1
    DOUBLE=$((SIZE * 2))
    sips -z $DOUBLE $DOUBLE "$ICON" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null 2>&1
done
sips -z 1024 1024 "$ICON" --out "$ICONSET/icon_512x512@2x.png" >/dev/null 2>&1
iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/icon.icns"
rm -rf "$ICONSET"

# Info.plist
sed "s/__VERSION__/$VERSION/g" "$DIR/packaging/macos/Info.plist.template" \
    > "$CONTENTS/Info.plist"

# Launcher
cp "$DIR/packaging/macos/launcher.sh" "$CONTENTS/MacOS/launcher"
chmod +x "$CONTENTS/MacOS/launcher"

# Editor
cp "$DIR/editor.py" "$CONTENTS/Resources/editor.py"

# Bundled themes
if [ -d "$DIR/themes" ]; then
    cp -r "$DIR/themes" "$CONTENTS/Resources/themes"
fi

echo "    Created $APP"

# ── Linux AppDir ───────────────────────────────────────────────────────────
echo "==> Building Bacalhau.AppDir ($VERSION)..."

APPDIR="$BUILD/Bacalhau.AppDir"
mkdir -p "$APPDIR/usr/bin"

cp "$DIR/packaging/linux/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "$DIR/packaging/linux/bacalhau.desktop" "$APPDIR/bacalhau.desktop"
cp "$ICON" "$APPDIR/bacalhau.png"
cp "$DIR/editor.py" "$APPDIR/usr/bin/editor.py"
if [ -d "$DIR/themes" ]; then
    cp -r "$DIR/themes" "$APPDIR/usr/bin/themes"
fi
mkdir -p "$APPDIR/usr/share/mime/packages"
cp "$DIR/packaging/linux/bacalhau-mime.xml" "$APPDIR/usr/share/mime/packages/"

# Build .AppImage if appimagetool is available
if command -v appimagetool >/dev/null 2>&1; then
    echo "    Running appimagetool..."
    ARCH="$(uname -m)"
    appimagetool "$APPDIR" "$BUILD/Bacalhau-${VERSION}-${ARCH}.AppImage" 2>/dev/null
    echo "    Created Bacalhau-${VERSION}-${ARCH}.AppImage"
else
    echo "    appimagetool not found — AppDir created but .AppImage not built."
    echo "    Get it: https://github.com/AppImage/appimagetool/releases"
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "Build complete ($VERSION):"
echo "  macOS: $APP"
if ls "$BUILD"/Bacalhau*.AppImage >/dev/null 2>&1; then
    echo "  Linux: $(ls "$BUILD"/Bacalhau*.AppImage)"
else
    echo "  Linux: $APPDIR (AppDir only)"
fi
