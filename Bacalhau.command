#!/bin/bash
# Bacalhau — double-click to launch the manuscript editor.
# Place this file (and editor.py) in your project folder, then double-click.
cd "$(dirname "$0")"
python3 "$(dirname "$0")/editor.py" .
