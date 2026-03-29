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
# 0. .bacalhau file passed as argument (file association double-click)
# 1. chapters/ next to the .app bundle (if it exists)
# 2. chapters/ in the current working directory (terminal launch)
# 3. No project — editor.py shows welcome screen
PROJECT=""
if [ -n "$1" ] && [ -f "$1" ] && echo "$1" | grep -q '\.bacalhau$'; then
    PROJECT="$1"
elif [ -d "$PARENT/chapters" ]; then
    PROJECT="$PARENT/chapters"
elif [ -d "$PWD/chapters" ]; then
    PROJECT="$PWD/chapters"
fi

# Launch and write PID file
LOGFILE="$HOME/.bacalhau.log"
python3 "$EDITOR" $PROJECT >"$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"

# Clean up PID file on exit
trap "rm -f '$PIDFILE'" EXIT
wait
