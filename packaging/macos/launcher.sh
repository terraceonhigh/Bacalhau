#!/bin/bash
# Bacalhau.app launcher — finds the project directory, then runs editor.py.

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PARENT="$(dirname "$APP_DIR")"
EDITOR="$(dirname "$0")/../Resources/editor.py"

# Check for Python 3
if ! command -v python3 >/dev/null 2>&1; then
    osascript -e 'display dialog "Bacalhau requires Python 3, which was not found on your system." buttons {"OK"} with title "Bacalhau" with icon stop' 2>/dev/null
    echo "Error: Python 3 is required but not found." >&2
    exit 1
fi

# Project directory resolution:
# 1. chapters/ next to the .app bundle
# 2. chapters/ in the current working directory
# 3. Create chapters/ next to the .app (or ~/Bacalhau/chapters/ if that fails)
if [ -d "$PARENT/chapters" ]; then
    PROJECT="$PARENT/chapters"
elif [ -d "$PWD/chapters" ]; then
    PROJECT="$PWD/chapters"
elif mkdir -p "$PARENT/chapters" 2>/dev/null; then
    PROJECT="$PARENT/chapters"
else
    mkdir -p "$HOME/Bacalhau/chapters"
    PROJECT="$HOME/Bacalhau/chapters"
fi

exec python3 "$EDITOR" "$PROJECT"
