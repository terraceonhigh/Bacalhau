#!/bin/bash
# Bacalhau — double-click to launch the manuscript editor.
# Place this file (and editor.py) in your project folder, then double-click.
#
# macOS: double-click this .command file in Finder
# Linux: use ./Bacalhau or double-click Bacalhau.desktop
#
cd "$(dirname "$0")"
echo "Starting Bacalhau..."
python3 "$(dirname "$0")/editor.py" .
