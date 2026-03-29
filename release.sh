#!/bin/bash
# Build all release artifacts for Bacalhau.
set -euo pipefail

VERSION="${1:-dev}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building Bacalhau $VERSION..."
cd "$DIR"

# Build native packages
./build.sh "$VERSION"

# ── Zip the .app bundle ────────────────────────────────────────────────────
echo ""
echo "==> Packaging release zips..."

# macOS native (.app)
cd "$DIR/build" && zip -r "$DIR/Bacalhau-${VERSION}-macos.zip" Bacalhau.app >/dev/null
echo "  Created Bacalhau-${VERSION}-macos.zip"
cd "$DIR"

# macOS legacy (flat files)
zip -j "Bacalhau-${VERSION}-macos-portable.zip" editor.py Bacalhau.command >/dev/null
echo "  Created Bacalhau-${VERSION}-macos-portable.zip"

# Linux legacy (flat files)
zip -j "Bacalhau-${VERSION}-linux-portable.zip" editor.py Bacalhau Bacalhau.desktop >/dev/null
echo "  Created Bacalhau-${VERSION}-linux-portable.zip"

# Copy AppImage if it was built
if ls "$DIR/build"/Bacalhau*.AppImage >/dev/null 2>&1; then
    cp "$DIR/build"/Bacalhau*.AppImage "$DIR/"
    echo "  Copied AppImage to repo root"
fi

echo ""
echo "Release artifacts:"
ls -lh Bacalhau-${VERSION}*.zip Bacalhau-${VERSION}*.AppImage 2>/dev/null || true
