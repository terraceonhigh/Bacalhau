"""Shared application state."""
import time

CHAPTERS_DIR = None
BACALHAU_FILE = None   # Path to .bacalhau file when opened from one
BACALHAU_NAME = None   # Original filename (for browser-opened projects)
TEMP_DIR = None        # Temp extraction dir (cleaned up on exit)
_last_heartbeat = time.time()
