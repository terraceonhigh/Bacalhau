#!/bin/bash
# Build Bacalhau.app — native macOS bundle with Wails webview.
set -euo pipefail

VERSION="${1:-dev}"
DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build"
ICON="$DIR/icons/icon.png"

# Clean
rm -rf "$BUILD"
mkdir -p "$BUILD"

if [ ! -f "$ICON" ]; then
    echo "Error: icons/icon.png not found." >&2
    exit 1
fi

echo "==> Compiling Go binary ($VERSION)..."
CGO_LDFLAGS="-framework UniformTypeIdentifiers" \
  go build -tags "desktop,production" -ldflags "-s -w -X main.version=$VERSION" \
  -o "$BUILD/bacalhau-bin" .

echo "==> Building Bacalhau.app..."
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

# Info.plist — point executable at the Go binary
sed "s/__VERSION__/$VERSION/g" "$DIR/packaging/macos/Info.plist.template" \
    | sed 's|<string>launcher</string>|<string>Bacalhau</string>|' \
    > "$CONTENTS/Info.plist"

# Binary
cp "$BUILD/bacalhau-bin" "$CONTENTS/MacOS/Bacalhau"
chmod +x "$CONTENTS/MacOS/Bacalhau"

# Clean up
rm "$BUILD/bacalhau-bin"

echo ""
echo "Done: $APP"
echo "Run:  open $APP"
