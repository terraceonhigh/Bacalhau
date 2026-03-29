#!/bin/bash
# Bacalhau.app launcher — finds the project directory, then runs editor.py.

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PARENT="$(dirname "$APP_DIR")"
EDITOR="$(dirname "$0")/../Resources/editor.py"
PIDFILE="$HOME/.bacalhau.pid"

# Kill any existing instance
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null
        sleep 0.5
    fi
    rm -f "$PIDFILE"
fi

# Check for Python 3
if ! command -v python3 >/dev/null 2>&1; then
    osascript -e 'display dialog "Bacalhau requires Python 3, which was not found on your system." buttons {"OK"} with title "Bacalhau" with icon stop' 2>/dev/null
    exit 1
fi

# Project directory resolution:
# 1. chapters/ next to the .app bundle (the intended workflow)
# 2. chapters/ in the current working directory (terminal launch)
# 3. Create chapters/ next to the .app
# 4. Last resort: ~/Bacalhau/chapters/
if [ -d "$PARENT/chapters" ]; then
    PROJECT="$PARENT/chapters"
elif [ -d "$PWD/chapters" ]; then
    PROJECT="$PWD/chapters"
elif mkdir -p "$PARENT/chapters" 2>/dev/null; then
    PROJECT="$PARENT/chapters"
    osascript -e "display notification \"Created chapters/ in $PARENT\" with title \"Bacalhau\"" 2>/dev/null
else
    mkdir -p "$HOME/Bacalhau/chapters"
    PROJECT="$HOME/Bacalhau/chapters"
    osascript -e 'display notification "Writing to ~/Bacalhau/chapters/" with title "Bacalhau"' 2>/dev/null
fi

# Launch and write PID file
python3 "$EDITOR" "$PROJECT" &
echo $! > "$PIDFILE"

# Clean up PID file on exit
trap "rm -f '$PIDFILE'" EXIT
wait
