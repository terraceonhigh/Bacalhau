#!/bin/bash
# Build release zips for Bacalhau.
# Output: per-platform zips + a universal zip
set -euo pipefail

VERSION="${1:-dev}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building Bacalhau $VERSION..."
cd "$DIR"

# macOS: editor.py + Bacalhau.command
zip -j "Bacalhau-${VERSION}-macos.zip" editor.py Bacalhau.command
echo "  Created Bacalhau-${VERSION}-macos.zip"

# Linux: editor.py + Bacalhau (shell) + Bacalhau.desktop
zip -j "Bacalhau-${VERSION}-linux.zip" editor.py Bacalhau Bacalhau.desktop
echo "  Created Bacalhau-${VERSION}-linux.zip"

# Universal: everything
zip -j "Bacalhau-${VERSION}.zip" editor.py Bacalhau Bacalhau.command Bacalhau.desktop
echo "  Created Bacalhau-${VERSION}.zip"

echo ""
echo "Done. Release artifacts:"
ls -lh Bacalhau-${VERSION}*.zip
