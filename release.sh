#!/bin/bash
# Build a release zip for Bacalhau.
# Output: Bacalhau.zip containing editor.py and Bacalhau.command
set -euo pipefail

VERSION="${1:-dev}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building Bacalhau $VERSION..."
cd "$DIR"

# Create a clean zip with just the two user-facing files
zip -j "Bacalhau-${VERSION}.zip" editor.py Bacalhau.command

echo "Created Bacalhau-${VERSION}.zip"
echo "Contents:"
unzip -l "Bacalhau-${VERSION}.zip"
