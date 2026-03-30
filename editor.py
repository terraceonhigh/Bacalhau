#!/usr/bin/env python3
"""
Bacalhau — Browser-based manuscript editor.

Zero external dependencies. Python 3 stdlib only.
Serves a three-pane editor (sidebar tree + editor + preview) on localhost:3000.

Reads a hierarchical directory of markdown files with _order.yaml per directory.

Usage:
    python3 editor.py <project-dir>              # open a project
    python3 editor.py <project-dir> --port 8080  # custom port
    python3 editor.py .                           # current directory

The project directory should contain markdown files and/or subdirectories,
each with an optional _order.yaml to control ordering.
"""

import http.server
import os
import shutil
import signal
import sys
import threading
import time

# Ensure sibling modules are importable regardless of cwd
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import state
from server import Handler
from helpers import _repack_bacalhau, _open_app_window


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Parse args
    args = sys.argv[1:]
    port = 3000
    project_dir = None

    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif not args[i].startswith("-"):
            project_dir = args[i]
            i += 1
        else:
            i += 1

    import atexit
    import tempfile

    if not project_dir:
        # No project specified — create empty temp dir for welcome state
        state.TEMP_DIR = tempfile.mkdtemp(prefix="bacalhau-empty-")
        state.CHAPTERS_DIR = os.path.join(state.TEMP_DIR, "chapters")
        os.makedirs(state.CHAPTERS_DIR)
        def _cleanup_temp():
            if state.TEMP_DIR and os.path.isdir(state.TEMP_DIR):
                shutil.rmtree(state.TEMP_DIR, ignore_errors=True)
        atexit.register(_cleanup_temp)
    else:
        # Handle .bacalhau file: extract to temp dir
        project_dir = os.path.abspath(project_dir)
        if project_dir.endswith(".bacalhau") and os.path.isfile(project_dir):
            import zipfile
            state.BACALHAU_FILE = project_dir
            state.TEMP_DIR = tempfile.mkdtemp(prefix="bacalhau-")
            with zipfile.ZipFile(state.BACALHAU_FILE, "r") as zf:
                # Zip-slip protection
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(state.TEMP_DIR, member))
                    if not target.startswith(os.path.realpath(state.TEMP_DIR) + os.sep) and target != os.path.realpath(state.TEMP_DIR):
                        print(f"Error: unsafe path in archive: {member}", file=sys.stderr)
                        sys.exit(1)
                zf.extractall(state.TEMP_DIR)
            chapters_path = os.path.join(state.TEMP_DIR, "chapters")
            if not os.path.isdir(chapters_path):
                print(f"Error: no chapters/ directory in {state.BACALHAU_FILE}", file=sys.stderr)
                shutil.rmtree(state.TEMP_DIR, ignore_errors=True)
                sys.exit(1)
            state.CHAPTERS_DIR = chapters_path
            def _cleanup_temp():
                if state.TEMP_DIR and os.path.isdir(state.TEMP_DIR):
                    shutil.rmtree(state.TEMP_DIR, ignore_errors=True)
            atexit.register(_cleanup_temp)
            print(f"Opened: {state.BACALHAU_FILE} → {state.TEMP_DIR}")
        else:
            state.CHAPTERS_DIR = project_dir
            if not os.path.isdir(state.CHAPTERS_DIR):
                print(f"Error: not a directory: {state.CHAPTERS_DIR}", file=sys.stderr)
                sys.exit(1)

    # Find an available port
    pid = os.getpid()
    server = None
    for attempt_port in range(port, port + 100):
        try:
            server = http.server.HTTPServer(("127.0.0.1", attempt_port), Handler)
            port = attempt_port
            break
        except OSError:
            continue
    if server is None:
        print("Error: no available port found", file=sys.stderr)
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"Bacalhau: {url} — editing {state.CHAPTERS_DIR}")
    print(f"PID: {pid} — kill with: kill {pid}")
    print("Press Ctrl+C to stop.")

    def shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down.")
        _repack_bacalhau()
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)

    # Heartbeat watchdog — shut down if browser disappears
    state._last_heartbeat = time.time()
    def _heartbeat_watchdog():
        time.sleep(30)  # Grace period for browser to connect
        while True:
            time.sleep(15)
            if time.time() - state._last_heartbeat > 120:
                print("\nNo heartbeat for 2 minutes — shutting down.", file=sys.stderr)
                _repack_bacalhau()
                os._exit(0)
    threading.Thread(target=_heartbeat_watchdog, daemon=True).start()

    threading.Timer(0.5, lambda: _open_app_window(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        _repack_bacalhau()
        server.server_close()


if __name__ == "__main__":
    main()
