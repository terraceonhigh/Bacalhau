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
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.parse

# Set by main() from command-line argument
CHAPTERS_DIR = None
BACALHAU_FILE = None   # Path to .bacalhau file when opened from one
TEMP_DIR = None        # Temp extraction dir (cleaned up on exit)
_last_heartbeat = time.time()


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_root():
    """Find the git root directory, or None."""
    if TEMP_DIR and CHAPTERS_DIR and CHAPTERS_DIR.startswith(TEMP_DIR):
        return None  # Temp-extracted .bacalhau — no git
    if CHAPTERS_DIR and os.path.isdir(os.path.join(CHAPTERS_DIR, ".git")):
        return CHAPTERS_DIR
    parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
    if parent and os.path.isdir(os.path.join(parent, ".git")):
        return parent
    return None


def _run_git(*args, cwd=None):
    """Run a git command and return (returncode, stdout, stderr)."""
    git_cwd = cwd or _git_root() or CHAPTERS_DIR
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=git_cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"


def _git_installed():
    """Check if git is available."""
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git_has_commits():
    """Check if the repo has at least one commit."""
    rc, _, _ = _run_git("rev-parse", "HEAD")
    return rc == 0


def _git_resolve_path(short_path):
    """Resolve a display path (relative to project) back to git-root-relative."""
    root = _git_root()
    if not root or not CHAPTERS_DIR:
        return short_path
    scope = CHAPTERS_DIR
    parent = os.path.dirname(CHAPTERS_DIR)
    if parent and parent != root:
        scope = parent
    if scope == root:
        return short_path
    rel_prefix = os.path.relpath(scope, root)
    return os.path.join(rel_prefix, short_path)


# ── Filesystem helpers ────────────────────────────────────────────────────────

def read_order(directory):
    """Read _order.yaml from a directory. Falls back to sorted listing."""
    order_file = os.path.join(directory, "_order.yaml")
    entries = []
    if os.path.exists(order_file):
        with open(order_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- "):
                    entry = line[2:].strip()
                    if entry:
                        entries.append(entry)
        # Append anything on disk but not listed
        on_disk = set()
        for name in os.listdir(directory):
            if name.startswith("_") or name.startswith("."):
                continue
            if os.path.isdir(os.path.join(directory, name)):
                on_disk.add(name + "/")
            elif name.endswith(".md"):
                on_disk.add(name)
        for extra in sorted(on_disk - set(entries)):
            entries.append(extra)
    else:
        for name in sorted(os.listdir(directory)):
            if name.startswith("_") or name.startswith("."):
                continue
            if os.path.isdir(os.path.join(directory, name)):
                entries.append(name + "/")
            elif name.endswith(".md"):
                entries.append(name)
    return entries


def _read_order_raw(directory):
    """Read _order.yaml entries without appending unlisted on-disk files."""
    order_file = os.path.join(directory, "_order.yaml")
    entries = []
    if os.path.exists(order_file):
        with open(order_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- "):
                    entry = line[2:].strip()
                    if entry:
                        entries.append(entry)
    return entries


def write_order(directory, entries):
    """Write _order.yaml in a directory."""
    path = os.path.join(directory, "_order.yaml")
    with open(path, "w") as f:
        for entry in entries:
            f.write(f"- {entry}\n")


def build_tree(directory, rel_prefix=""):
    """Build a recursive tree structure for the API."""
    nodes = []
    for entry in read_order(directory):
        if entry.endswith("/"):
            dirname = entry.rstrip("/")
            dirpath = os.path.join(directory, dirname)
            rel = os.path.join(rel_prefix, dirname) if rel_prefix else dirname
            if os.path.isdir(dirpath):
                # Get heading from _part.md if it exists
                part_file = os.path.join(dirpath, "_part.md")
                heading = dirname.replace("-", " ").title()
                if os.path.exists(part_file):
                    heading = get_heading(part_file) or heading
                children = build_tree(dirpath, rel)
                nodes.append({
                    "type": "dir",
                    "name": dirname,
                    "path": rel,
                    "heading": heading,
                    "children": children,
                })
        else:
            filepath = os.path.join(directory, entry)
            rel = os.path.join(rel_prefix, entry) if rel_prefix else entry
            if os.path.exists(filepath):
                heading = get_heading(filepath) or entry
                writable = os.access(filepath, os.W_OK)
                nodes.append({
                    "type": "file",
                    "name": entry,
                    "path": rel,
                    "heading": heading,
                    "writable": writable,
                })
    return nodes


def get_heading(filepath):
    """Get first heading from a markdown file."""
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.startswith("#"):
                    return line.lstrip("#").strip()
    except (FileNotFoundError, PermissionError):
        pass
    return None


def _bundled_themes_dir():
    """Return the themes/ directory bundled with the app."""
    # Check next to editor.py (repo or app bundle Resources/)
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes")
    return d if os.path.isdir(d) else None


def _user_themes_dir():
    """Return the platform-appropriate user themes directory. Create if absent."""
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Bacalhau")
    else:
        xdg = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
        base = os.path.join(xdg, "Bacalhau")
    d = os.path.join(base, "themes")
    os.makedirs(d, exist_ok=True)
    return d


def list_themes():
    """List available .css theme files from bundled + user dirs."""
    seen = set()
    themes = []
    # User themes take priority (listed first so they override bundled)
    for d in (_user_themes_dir(), _bundled_themes_dir()):
        if d and os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".css") and f not in seen:
                    seen.add(f)
                    themes.append(f)
    return sorted(themes)


def find_theme(name):
    """Find a theme CSS file by name, checking user dir first, then bundled."""
    for d in (_user_themes_dir(), _bundled_themes_dir()):
        if d:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    return None


def walk_files(directory):
    """Recursively yield all .md file paths in order."""
    for entry in read_order(directory):
        if entry.endswith("/"):
            subdir = os.path.join(directory, entry.rstrip("/"))
            if os.path.isdir(subdir):
                yield from walk_files(subdir)
        else:
            path = os.path.join(directory, entry)
            if os.path.exists(path):
                yield path


def resolve_path(relpath):
    """Resolve a relative path to an absolute path inside chapters/."""
    clean = urllib.parse.unquote(relpath)
    abspath = os.path.normpath(os.path.join(CHAPTERS_DIR, clean))
    if not abspath.startswith(CHAPTERS_DIR):
        raise ValueError("Path escape attempt")
    return abspath


# ── HTML Template ─────────────────────────────────────────────────────────────

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bacalhau</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
:root {
  --bg: #111; --bg2: #1a1a1a; --bg3: #222; --bg4: #2a2a2a;
  --fg: #e0e0e0; --fg2: #aaa; --fg3: #666;
  --accent: #5b9bd5; --gold: #b59b5b; --purple: #9b5bb5;
  --green: #1a5a1a; --green2: #2a7a2a;
  --border: #333;
}
* { box-sizing: border-box; margin: 0; padding: 0; }

/* Scrollbars — WebKit (Chrome, Safari, Edge) */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--fg3); }
/* Firefox */
* { scrollbar-width: thin; scrollbar-color: var(--border) var(--bg); }
body { font-family: -apple-system, "Helvetica Neue", sans-serif; background: var(--bg); color: var(--fg); display: flex; height: 100vh; overflow: hidden; }

/* ── Sidebar ── */
.sidebar {
  width: 300px; min-width: 300px; background: var(--bg2);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
.sidebar-header { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; }
.sidebar-header img { width: 32px; height: 32px; border-radius: 4px; flex-shrink: 0; }
.sidebar-header div { flex: 1; }
.sidebar-header h1 { font-size: 16px; font-weight: 600; margin-bottom: 2px; }
.sidebar-header p { font-size: 11px; color: var(--fg3); }
.tree { list-style: none; overflow-y: auto; flex: 1; padding: 8px; }
.tree ul { list-style: none; padding-left: 16px; }
.tree > ul { padding-left: 0; }

.tree-item {
  padding: 4px 8px; margin: 1px 0; background: transparent;
  border-radius: 3px; cursor: pointer; font-size: 12px;
  display: flex; align-items: center; gap: 4px;
  transition: background 0.1s; user-select: none;
}
.tree-item:hover { background: var(--bg3); }
.tree-item.active { background: var(--bg4); border: 1px solid var(--accent); }
.tree-item.dragging { opacity: 0.3; }
.tree-item.drag-over { border-top: 2px solid var(--accent); }
.tree-item.drag-into { background: var(--bg4); outline: 1px dashed var(--accent); }
.tree-item .toggle { width: 14px; text-align: center; font-size: 10px; color: var(--fg3); flex-shrink: 0; }
.tree-item .icon { width: 14px; text-align: center; font-size: 11px; flex-shrink: 0; }
.tree-item .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.tree-item .count { font-size: 10px; color: var(--fg3); margin-left: 4px; }
.tree-item.readonly { opacity: 0.6; font-style: italic; }
.tree-item.dir { font-weight: 600; }
.tree-item .meatball {
  display: none; gap: 1px; align-items: center; margin-left: auto; flex-shrink: 0;
}
.tree-item:hover .meatball { display: flex; }
.tree-item .mb {
  width: 20px; height: 18px; padding: 0; border-radius: 2px;
  font-size: 9px; line-height: 1; background: transparent;
  border: 1px solid transparent; color: var(--fg3); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.tree-item .mb:hover { background: var(--bg4); border-color: var(--border); color: var(--fg); }

.sidebar-footer {
  padding: 12px; border-top: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 6px;
}
.status { font-size: 11px; color: var(--fg3); min-height: 16px; }
.word-count { font-size: 11px; color: var(--fg3); display: flex; justify-content: space-between; }
button {
  padding: 6px 14px; border: 1px solid var(--border); border-radius: 3px;
  background: var(--bg3); color: var(--fg); cursor: pointer;
  font-size: 12px; transition: background 0.1s; width: 100%;
}
button:hover { background: var(--bg4); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover { opacity: 0.85; }

.welcome-overlay {
  position: fixed; inset: 0; z-index: 100;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg);
}
.welcome-card { text-align: center; }

/* ── Editor ── */
.editor-pane {
  flex: 1; display: flex; flex-direction: column;
  border-right: 1px solid var(--border); min-width: 0;
  overflow: hidden;
}
.editor-scroll {
  flex: 1; overflow-y: auto; background: var(--bg);
}
.file-section { position: relative; }
.file-header {
  position: sticky; top: 0; z-index: 2;
  padding: 4px 16px; background: var(--bg2);
  border-top: 1px solid var(--accent); border-bottom: 1px solid var(--accent);
  font-size: 11px; color: var(--fg3); font-family: monospace;
  display: flex; justify-content: space-between; align-items: center;
  cursor: pointer;
}
.file-header:hover { color: var(--fg2); }
.file-header.active { color: var(--accent); border-left: 2px solid var(--accent); }
.file-header .save-indicator { font-size: 10px; }
.file-header .save-indicator.unsaved { color: var(--accent); }
.file-section textarea {
  width: 100%; resize: none; border: none; outline: none;
  background: var(--bg); color: var(--fg);
  font-family: Charter, Georgia, serif; font-size: 17px; line-height: 1.7;
  padding: 16px 32px; tab-size: 4;
  overflow: hidden; min-height: 3em;
}
.file-section textarea:read-only { opacity: 0.6; font-style: italic; }

/* ── Insert Zones ── */
.insert-zone {
  height: 6px; position: relative; cursor: default;
  transition: height 0.15s; list-style: none;
  border-top: 2px solid transparent;
}
.insert-zone:hover { height: 20px; }
.insert-zone.drag-hover { height: 16px; border-top-color: var(--accent); }
.insert-zone::before {
  content: ''; position: absolute; left: 20px; right: 20px; top: 50%;
  height: 1px; background: transparent; transition: background 0.1s;
}
.insert-zone:hover::before { background: var(--accent); }
.insert-zone .iz-buttons {
  display: none; position: absolute; left: 50%; top: 50%;
  transform: translate(-50%, -50%); gap: 4px; z-index: 1;
}
.insert-zone:hover .iz-buttons { display: flex; }
.iz-btn {
  font-size: 9px; padding: 1px 6px; border-radius: 2px;
  background: var(--bg3); border: 1px solid var(--accent); color: var(--accent);
  cursor: pointer; white-space: nowrap;
}
.iz-btn:hover { background: var(--accent); color: #fff; }

/* ── Sync Bar ── */
.sync-bar {
  width: 2px; min-width: 2px; background: var(--accent);
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 6px;
  position: relative; z-index: 3;
}
.sync-btn {
  width: 26px; height: 26px; padding: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; line-height: 1; background: var(--bg2);
  border: 2px solid var(--accent); color: var(--fg2); cursor: pointer;
  position: relative;
}
.sync-btn:hover { background: var(--bg4); color: var(--fg); }
.sync-btn.toggle.active { background: var(--accent); border-color: var(--accent); color: #fff; }

/* ── Preview ── */
.preview-pane { flex: 1; overflow-y: auto; background: var(--bg); min-width: 0; }
.preview-content {
  max-width: 38em; margin: 0 auto; padding: 32px 24px;
  font-family: Charter, Georgia, serif; font-size: 16px;
  line-height: 1.65; color: var(--fg);
}
.preview-content h1 { font-size: 28px; text-align: center; margin: 1em 0 1.5em; font-family: "Gill Sans", "Helvetica Neue", sans-serif; }
.preview-content h2 { font-size: 18px; text-align: center; margin: 3em 0 1em; font-family: "Gill Sans", "Helvetica Neue", sans-serif; color: var(--fg2); letter-spacing: 0.05em; text-transform: uppercase; font-weight: 400; }
.preview-content h3 { font-size: 16px; margin: 2.5em 0 1em; text-align: center; font-family: "Gill Sans", "Helvetica Neue", sans-serif; font-weight: 400; font-style: italic; color: var(--fg2); }
.preview-content p { margin: 1em 0; text-align: justify; hyphens: auto; }
.preview-content hr { border: none; text-align: center; margin: 2em 0; }
.preview-content hr::after { content: "\\2217\\2003\\2217\\2003\\2217"; color: var(--fg3); font-size: 14px; }
.preview-content em { font-style: italic; }
.preview-content strong { font-weight: 700; }
.preview-content code { font-family: monospace; background: var(--bg3); padding: 1px 4px; border-radius: 2px; font-size: 0.9em; }
.preview-content .chapter-anchor { display: block; position: relative; top: -20px; }

/* ── Sidebar Tabs ── */
.sidebar-tabs {
  display: flex; border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.sidebar-tab {
  flex: 1; padding: 8px 0; border: none; border-bottom: 2px solid transparent;
  background: transparent; color: var(--fg3); font-size: 12px;
  cursor: pointer; text-align: center; width: auto;
}
.sidebar-tab:hover { color: var(--fg2); background: transparent; }
.sidebar-tab.active { color: var(--fg); border-bottom-color: var(--accent); }
.tab-badge {
  display: inline-block; min-width: 16px; height: 16px; line-height: 16px;
  border-radius: 8px; background: var(--accent); color: #fff;
  font-size: 10px; text-align: center; margin-left: 4px;
  padding: 0 4px; vertical-align: middle;
}

/* ── Git Panel ── */
.git-panel {
  flex: 1; overflow-y: auto; padding: 8px; font-size: 12px;
}
.git-message {
  padding: 16px; color: var(--fg2); text-align: center; font-size: 12px; line-height: 1.5;
}
.git-section-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 8px 4px; color: var(--fg2); font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.03em;
}
.git-section-header button {
  width: auto; padding: 2px 6px; font-size: 10px; background: transparent;
  border: 1px solid var(--border); border-radius: 2px; color: var(--fg3);
  cursor: pointer;
}
.git-section-header button:hover { background: var(--bg3); color: var(--fg); }
.git-file {
  display: flex; align-items: center; padding: 3px 8px; border-radius: 3px; gap: 6px;
}
.git-file:hover { background: var(--bg3); }
.git-badge {
  display: inline-block; width: 16px; text-align: center;
  font-weight: 700; font-size: 11px; flex-shrink: 0; font-family: monospace;
}
.git-badge.M { color: var(--accent); }
.git-badge.A { color: #5bb55b; }
.git-badge.D { color: #b55b5b; }
.git-badge.Q { color: var(--fg3); }
.git-badge.R { color: #b59b5b; }
.git-file .git-path {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--fg);
}
.git-file .git-action {
  width: auto; height: 18px; padding: 0 4px; border-radius: 2px;
  font-size: 12px; line-height: 1; background: transparent;
  border: 1px solid transparent; color: var(--fg3); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: opacity 0.1s;
}
.git-file:hover .git-action { opacity: 1; }
.git-file .git-action:hover { background: var(--bg4); border-color: var(--border); color: var(--fg); }
.git-commit-area {
  margin-top: 12px; padding: 0 4px; display: flex; flex-direction: column; gap: 6px;
}
.git-commit-area input {
  width: 100%; padding: 6px 8px; background: var(--bg3); color: var(--fg);
  border: 1px solid var(--border); border-radius: 3px; font-size: 12px;
  box-sizing: border-box;
}
.git-commit-area input:focus { border-color: var(--accent); outline: none; }

/* ── Git History ── */
.git-history { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 8px; }
.git-commit-item {
  padding: 6px 8px; border-radius: 3px; cursor: default;
}
.git-commit-item:hover { background: var(--bg3); }
.git-commit-msg {
  font-size: 12px; color: var(--fg); line-height: 1.4;
  word-wrap: break-word;
}
.git-commit-meta {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 2px;
}
.git-commit-when { font-size: 10px; color: var(--fg3); }
.git-commit-meta .git-action { opacity: 0; transition: opacity 0.1s; }
.git-commit-item:hover .git-commit-meta .git-action { opacity: 1; }

/* ── Browse Modal ── */
.browse-overlay {
  position: fixed; inset: 0; z-index: 101;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.6);
}
.browse-modal {
  background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
  width: 480px; max-height: 70vh; display: flex; flex-direction: column;
}
.browse-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}
.browse-title { font-size: 14px; font-weight: 600; color: var(--fg); }
.browse-close {
  width: auto; padding: 0 6px; background: transparent; border: none;
  color: var(--fg3); font-size: 18px; cursor: pointer; line-height: 1;
}
.browse-close:hover { color: var(--fg); background: transparent; }
.browse-breadcrumb {
  padding: 8px 16px; font-size: 12px; color: var(--fg3);
  border-bottom: 1px solid var(--border); overflow-x: auto; white-space: nowrap;
}
.browse-breadcrumb span { cursor: pointer; color: var(--fg2); }
.browse-breadcrumb span:hover { color: var(--accent); }
.browse-breadcrumb .sep { cursor: default; color: var(--fg3); margin: 0 2px; }
.browse-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.browse-item {
  display: flex; align-items: center; padding: 6px 16px; gap: 8px;
  cursor: pointer;
}
.browse-item:hover { background: var(--bg3); }
.browse-item.is-project { border-left: 2px solid var(--accent); }
.browse-icon { flex-shrink: 0; font-size: 14px; }
.browse-name { flex: 1; font-size: 13px; color: var(--fg); }
.browse-hint { font-size: 11px; color: var(--fg3); }
.browse-empty { padding: 24px 16px; text-align: center; color: var(--fg3); font-size: 12px; }
.browse-footer {
  padding: 12px 16px; border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
}
.browse-footer button { width: auto; }
.browse-actions { display: flex; gap: 6px; }
</style>
<link id="theme-css" rel="stylesheet" href="">
</head>
<body>

<div class="sidebar">
  <div class="sidebar-header">
    <img src="/favicon.png" alt="">
    <div>
      <h1>Bacalhau</h1>
      <p>Click to edit. Drag to reorder.</p>
    </div>
  </div>
  <div class="sidebar-tabs">
    <button class="sidebar-tab active" data-panel="files" onclick="switchPanel('files')">Files</button>
    <button class="sidebar-tab" data-panel="git" onclick="switchPanel('git')">Git <span id="gitBadge" class="tab-badge" style="display:none"></span></button>
  </div>
  <div class="tree" id="tree"></div>
  <div class="git-panel" id="gitPanel" style="display:none">
    <div id="gitContent"></div>
  </div>
  <div class="sidebar-footer">
    <input type="file" id="openInput" accept=".bacalhau" style="display:none" onchange="handleOpenFile(this)">
    <input type="file" id="themeInput" accept=".css" style="display:none" onchange="handleImportTheme(this)">
    <div style="display:flex;gap:4px;">
      <button style="flex:1" onclick="document.getElementById('openInput').click()">Open File</button>
      <button style="flex:1" onclick="openBrowse()">Browse</button>
    </div>
    <select id="themeSelect" onchange="switchTheme(this.value)" style="width:100%;padding:5px;background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:3px;font-size:12px;">
      <option value="">No theme</option>
    </select>
    <select id="saveAs" onchange="handleSaveAs(this.value); this.value='';" style="width:100%;padding:6px 14px;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:3px;font-size:12px;cursor:pointer;">
      <option value="" disabled selected>Save As\u2026</option>
      <option value="bacalhau">.bacalhau</option>
      <option value="zip">.zip</option>
      <option value="md">.md</option>
      <option value="pdf">.pdf</option>
    </select>
    <div class="word-count" id="wordCount"></div>
    <div class="status" id="status"></div>
  </div>
</div>

<div class="editor-pane" id="editorPane">
  <div class="editor-scroll" id="editorScroll"></div>
</div>

<div id="welcomeOverlay" class="welcome-overlay" style="display:none">
  <div class="welcome-card">
    <h2 style="color:var(--fg);margin:0 0 8px;font-size:22px;">Bacalhau</h2>
    <p style="color:var(--fg2);margin:0 0 20px;font-size:13px;">Open a manuscript to get started.</p>
    <button style="margin:4px;padding:10px 24px;" onclick="document.getElementById('openInput').click()">Open .bacalhau File</button>
    <button style="margin:4px;padding:10px 24px;" onclick="openBrowse()">Browse Folder</button>
    <button style="margin:4px;padding:10px 24px;" onclick="document.getElementById('welcomeOverlay').style.display='none';newFile('')">New Project</button>
  </div>
</div>

<div id="browseOverlay" class="browse-overlay" style="display:none">
  <div class="browse-modal">
    <div class="browse-header">
      <span class="browse-title">Open Folder</span>
      <button class="browse-close" onclick="closeBrowse()">&times;</button>
    </div>
    <div class="browse-breadcrumb" id="browseBreadcrumb"></div>
    <div class="browse-list" id="browseList"></div>
    <div class="browse-footer">
      <div class="browse-hint" id="browseFooterHint"></div>
      <div class="browse-actions">
        <button onclick="closeBrowse()">Cancel</button>
        <button class="primary" onclick="browseOpenHere()">Open Here</button>
      </div>
    </div>
  </div>
</div>

<div class="sync-bar">
  <button class="sync-btn" title="Scroll preview to current chapter" onclick="syncEditorToPreview()">&#x25B6;</button>
  <button class="sync-btn toggle" id="syncToggle" title="Toggle linked scrolling" onclick="toggleSync()">
    <span id="syncIcon">&#x1F517;</span>
  </button>
  <button class="sync-btn" title="Load visible chapter in editor" onclick="syncPreviewToEditor()">&#x25C0;</button>
</div>

<div class="preview-pane" id="previewPane">
  <div class="preview-content" id="preview"></div>
</div>

<script>
let tree = [];
let activeFile = null;
let dirty = false;  // legacy — per-file flags in fileDirtyFlags
let saveTimer = null;  // legacy — per-file timers in fileSaveTimers
let collapsed = JSON.parse(localStorage.getItem('bc-collapsed') || '{}');
let dragItem = null;
let currentPanel = 'files';
let gitState = null;
let gitLog = [];

// ── API ──────────────────────────────────────────────────────────────────────
async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

// ── Markdown parser ──────────────────────────────────────────────────────────
function md(text) {
  let html = '';
  for (let block of text.split(/\\n{2,}/)) {
    block = block.trim();
    if (!block) continue;
    const hm = block.match(/^(#{1,6})\\s+(.+)$/);
    if (hm) { html += '<h'+hm[1].length+'>'+inline(hm[2])+'</h'+hm[1].length+'>\\n'; continue; }
    if (/^---+$/.test(block)||/^\\*\\*\\*+$/.test(block)) { html += '<hr>\\n'; continue; }
    html += '<p>'+inline(block.replace(/\\n/g,' '))+'</p>\\n';
  }
  return html;
}
function inline(t) {
  t = t.replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>');
  t = t.replace(/\\*(.+?)\\*/g,'<em>$1</em>');
  t = t.replace(/`([^`]+?)`/g,'<code>$1</code>');
  return t;
}

// ── Tree rendering ───────────────────────────────────────────────────────────
async function loadTree() {
  const data = await api('/api/tree');
  tree = data.tree;
  const welcome = document.getElementById('welcomeOverlay');
  if (tree.length === 0) {
    welcome.style.display = 'flex';
  } else {
    welcome.style.display = 'none';
  }
  renderTree();
  await buildEditor();
  renderPreview();
  updateWordCount();
  refreshGit();
}

function renderTree() {
  const container = document.getElementById('tree');
  container.innerHTML = '';
  // Assign scene numbers before rendering
  let sceneNum = 0;
  assignSceneNumbers(tree, n => ++sceneNum);
  container.appendChild(buildTreeUL(tree, ''));
}

function assignSceneNumbers(nodes, nextNum) {
  for (const node of nodes) {
    if (node.type === 'dir') {
      assignSceneNumbers(node.children || [], nextNum);
    } else {
      // Scenes get numbers; _part.md and intermezzos don't
      if (node.name !== '_part.md' && !node.name.startsWith('intermezzo-') && node.name !== 'title.md') {
        node.sceneNum = nextNum();
      } else {
        node.sceneNum = null;
      }
    }
  }
}

function makeInsertZone(parentPath, position) {
  const zone = document.createElement('li');
  zone.className = 'insert-zone';
  // Drag-and-drop target
  zone.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; zone.classList.add('drag-hover'); });
  zone.addEventListener('dragleave', () => { zone.classList.remove('drag-hover'); });
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-hover'); onDrop(parentPath, position); });
  // Hover buttons for new file / new folder
  const buttons = document.createElement('span');
  buttons.className = 'iz-buttons';
  const btnFile = document.createElement('span');
  btnFile.className = 'iz-btn';
  btnFile.textContent = '+file';
  btnFile.addEventListener('click', e => { e.stopPropagation(); newFileAt(parentPath, position); });
  const btnDir = document.createElement('span');
  btnDir.className = 'iz-btn';
  btnDir.textContent = '+folder';
  btnDir.addEventListener('click', e => { e.stopPropagation(); newDirAt(parentPath, position); });
  buttons.appendChild(btnFile);
  buttons.appendChild(btnDir);
  zone.appendChild(buttons);
  return zone;
}

function buildTreeUL(nodes, parentPath) {
  const ul = document.createElement('ul');
  nodes.forEach((node, i) => {
    // Insert zone before each item
    ul.appendChild(makeInsertZone(parentPath, i));

    const li = document.createElement('li');

    if (node.type === 'dir') {
      const isCollapsed = collapsed[node.path];
      const row = document.createElement('div');
      row.className = 'tree-item dir';
      row.draggable = true;
      row.dataset.path = node.path;
      row.dataset.type = 'dir';
      const childCount = countFiles(node);
      row.innerHTML =
        '<span class="toggle">'+(isCollapsed ? '\\u25B6' : '\\u25BC')+'</span>' +
        '<span class="icon">\\uD83D\\uDCC1</span>' +
        '<span class="label">'+esc(node.heading)+'</span>' +
        '<span class="count">'+childCount+'</span>' +
        '<span class="meatball">' +
          '<span class="mb" title="Duplicate folder" data-action="cpdir">cp</span>' +
          '<span class="mb" title="New file here" data-action="newfile">+f</span>' +
          '<span class="mb" title="New subfolder" data-action="newdir">+d</span>' +
          '<span class="mb" title="Delete folder" data-action="rmdir">rm</span>' +
        '</span>';

      // Toggle collapse
      row.querySelector('.toggle').addEventListener('click', e => {
        e.stopPropagation();
        collapsed[node.path] = !collapsed[node.path];
        localStorage.setItem('bc-collapsed', JSON.stringify(collapsed));
        renderTree();
      });

      // Double-click label to rename
      row.querySelector('.label').addEventListener('dblclick', e => {
        e.stopPropagation();
        startInlineRename(row, node.path, node.name, 'dir');
      });

      // Meatball actions
      row.querySelectorAll('.mb').forEach(btn => {
        btn.addEventListener('mousedown', e => e.stopPropagation());
        btn.addEventListener('click', e => {
          e.stopPropagation();
          const action = btn.dataset.action;
          if (action === 'cpdir') copyDir(node.path);
          else if (action === 'newfile') newFile(node.path);
          else if (action === 'newdir') newDir(node.path);
          else if (action === 'rmdir') removeDir(node.path);
        });
      });

      // Drag
      row.addEventListener('dragstart', e => { dragItem = {type:'dir', path:node.path}; e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); });
      row.addEventListener('dragend', () => { clearAllDragState(); dragItem=null; });
      // Drop on dir = insert into end of dir
      row.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; row.classList.add('drag-into'); });
      row.addEventListener('dragleave', () => { row.classList.remove('drag-into'); });
      row.addEventListener('drop', e => { e.preventDefault(); row.classList.remove('drag-into'); onDrop(node.path, -1); });

      li.appendChild(row);

      if (!isCollapsed) {
        li.appendChild(buildTreeUL(node.children || [], node.path));
      }
    } else {
      const row = document.createElement('div');
      row.className = 'tree-item' + (activeFile === node.path ? ' active' : '') + (!node.writable ? ' readonly' : '');
      row.draggable = true;
      row.dataset.path = node.path;
      row.dataset.type = 'file';
      const lockIcon = node.writable ? '\\uD83D\\uDD13' : '\\uD83D\\uDD12';
      row.innerHTML =
        '<span class="toggle"></span>' +
        '<span class="icon">\\uD83D\\uDCC4</span>' +
        '<span class="label">'+(node.sceneNum ? node.sceneNum+'. ' : '')+esc(node.heading)+'</span>' +
        '<span class="meatball">' +
          '<span class="mb" title="Duplicate" data-action="cp">cp</span>' +
          '<span class="mb" title="Delete" data-action="rm">rm</span>' +
          '<span class="mb" title="Toggle read-only" data-action="lock">'+lockIcon+'</span>' +
        '</span>';

      let fileDrag = false;
      let clickTimer = null;
      row.addEventListener('mousedown', () => { fileDrag = false; });
      row.addEventListener('mouseup', e => {
        if (fileDrag || renaming || e.target.closest('.meatball')) return;
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => selectFile(node.path), 200);
      });

      // Double-click label to rename
      row.addEventListener('dblclick', e => {
        if (e.target.closest('.meatball')) return;
        clearTimeout(clickTimer);
        startInlineRename(row, node.path, node.name, 'file');
      });

      row.querySelectorAll('.mb').forEach(btn => {
        btn.addEventListener('mousedown', e => e.stopPropagation());
        btn.addEventListener('mouseup', e => {
          e.stopPropagation();
          const action = btn.dataset.action;
          if (action === 'cp') copyFile(node.path);
          else if (action === 'rm') removeFile(node.path);
          else if (action === 'lock') toggleLock(node.path);
        });
      });

      row.addEventListener('dragstart', e => { fileDrag = true; dragItem = {type:'file', path:node.path}; e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); });
      row.addEventListener('dragend', () => { clearAllDragState(); dragItem=null; });

      li.appendChild(row);
    }
    ul.appendChild(li);
  });
  // Final insert zone
  ul.appendChild(makeInsertZone(parentPath, nodes.length));
  return ul;
}

function countFiles(node) {
  if (node.type === 'file') return 1;
  return (node.children||[]).reduce((s,c) => s + countFiles(c), 0);
}

// ── Drag and drop ────────────────────────────────────────────────────────────
function clearAllDragState() {
  document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));
  document.querySelectorAll('.drag-into').forEach(el => el.classList.remove('drag-into'));
  document.querySelectorAll('.drag-hover').forEach(el => el.classList.remove('drag-hover'));
}

async function onDrop(targetDir, position) {
  if (!dragItem) return;
  // Don't drop on yourself
  if (dragItem.path === targetDir) return;
  // Don't drop a dir into its own subtree
  if (dragItem.type === 'dir' && targetDir.startsWith(dragItem.path + '/')) return;
  setStatus('Moving...');
  const data = await api('/api/tree/move', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({src: dragItem.path, src_type: dragItem.type, dest_dir: targetDir, position})
  });
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  await loadTree();
}

// ── Editor (continuous scroll) ───────────────────────────────────────────────
let selectGuard = false;
let fileSaveTimers = {};  // per-file debounce timers
let fileDirtyFlags = {};  // per-file dirty state

async function buildEditor() {
  const data = await api('/api/preview');
  const container = document.getElementById('editorScroll');
  container.innerHTML = '';
  fileSaveTimers = {};
  fileDirtyFlags = {};

  for (const f of data.files) {
    const section = document.createElement('div');
    section.className = 'file-section';
    section.dataset.path = f.path;

    // Header bar
    const header = document.createElement('div');
    header.className = 'file-header' + (activeFile === f.path ? ' active' : '');
    header.dataset.path = f.path;
    const fname = f.path.split('/').pop();
    const node = findNode(tree, f.path);
    const writable = node ? node.writable : true;
    header.innerHTML = '<span>' + esc(fname) + (writable ? '' : ' (read-only)') + '</span><span class="save-indicator" id="save-' + esc(f.path.replace(/[\\/\\.]/g, '-')) + '"></span>';
    header.addEventListener('click', () => {
      activeFile = f.path;
      renderTree();
      highlightActiveHeader();
    });
    section.appendChild(header);

    // Textarea
    const ta = document.createElement('textarea');
    ta.value = f.content;
    ta.readOnly = !writable;
    ta.spellcheck = true;
    ta.dataset.path = f.path;

    // Auto-resize
    function autoResize() {
      ta.style.height = 'auto';
      ta.style.height = ta.scrollHeight + 'px';
    }
    ta.addEventListener('input', () => {
      autoResize();
      fileDirtyFlags[f.path] = true;
      const ind = document.getElementById('save-' + f.path.replace(/[\\/\\.]/g, '-'));
      if (ind) { ind.textContent = '\\u25CF'; ind.className = 'save-indicator unsaved'; }
      clearTimeout(fileSaveTimers[f.path]);
      fileSaveTimers[f.path] = setTimeout(() => saveFileByPath(f.path), 1000);
      schedulePreviewUpdate();
      updateWordCount();
    });
    ta.addEventListener('focus', () => {
      activeFile = f.path;
      renderTree();
      highlightActiveHeader();
      updateWordCount();
    });
    ta.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      const val = ta.value;
      const pos = ta.selectionStart;
      if (e.key === 'ArrowUp' && pos === 0) {
        // At the very start — move to previous textarea's end
        const prev = ta.closest('.file-section').previousElementSibling;
        if (prev) {
          const prevTa = prev.querySelector('textarea');
          if (prevTa) { e.preventDefault(); prevTa.focus(); prevTa.selectionStart = prevTa.selectionEnd = prevTa.value.length; }
        }
      } else if (e.key === 'ArrowDown' && pos === val.length) {
        // At the very end — move to next textarea's start
        const next = ta.closest('.file-section').nextElementSibling;
        if (next) {
          const nextTa = next.querySelector('textarea');
          if (nextTa) { e.preventDefault(); nextTa.focus(); nextTa.selectionStart = nextTa.selectionEnd = 0; }
        }
      }
    });
    section.appendChild(ta);
    container.appendChild(section);

    // Initial auto-resize after appending to DOM
    requestAnimationFrame(autoResize);
  }
}

function highlightActiveHeader() {
  document.querySelectorAll('.file-header').forEach(h => {
    h.classList.toggle('active', h.dataset.path === activeFile);
  });
}

async function selectFile(path) {
  selectGuard = true;
  setTimeout(() => { selectGuard = false; }, 800);
  activeFile = path;
  renderTree();
  highlightActiveHeader();
  // Scroll editor to this file's section
  const section = document.querySelector('.file-section[data-path="' + path + '"]');
  if (section) section.scrollIntoView({behavior:'smooth', block:'start'});
  // Scroll preview
  const slug = path.replace(/[\/\\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (anchor) anchor.scrollIntoView({behavior:'smooth', block:'start'});
}

async function saveFileByPath(path) {
  if (!fileDirtyFlags[path]) return;
  const ta = document.querySelector('textarea[data-path="' + path + '"]');
  if (!ta) return;
  const content = ta.value;
  const ind = document.getElementById('save-' + path.replace(/[\\/\\.]/g, '-'));
  if (ind) { ind.textContent = 'saving...'; ind.className = 'save-indicator'; }
  const data = await api('/api/chapter/' + encodeURI(path), {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({content})
  });
  if (data.error) { if (ind) ind.textContent = data.error; return; }
  fileDirtyFlags[path] = false;
  if (ind) { ind.textContent = 'saved'; ind.className = 'save-indicator'; }
  setTimeout(() => { if (!fileDirtyFlags[path] && ind) ind.textContent = ''; }, 2000);
  renderPreview();
  refreshGit();
}

async function saveFile() {
  // Save all dirty files
  for (const path of Object.keys(fileDirtyFlags)) {
    if (fileDirtyFlags[path]) await saveFileByPath(path);
  }
}

// Track which file is visible in the editor scroll + sync
function getVisibleEditorFile() {
  const editorScroll = document.getElementById('editorScroll');
  if (!editorScroll) return null;
  const rect = editorScroll.getBoundingClientRect();
  const midY = rect.top + rect.height * 0.5;
  let best = null, bestDist = Infinity;
  document.querySelectorAll('.file-section').forEach(s => {
    const d = Math.abs(s.getBoundingClientRect().top - midY);
    if (d < bestDist) { bestDist = d; best = s.dataset.path; }
  });
  return best;
}

document.addEventListener('DOMContentLoaded', () => {
  const editorScroll = document.getElementById('editorScroll');
  if (!editorScroll) return;
  let editorSyncPending = false;
  editorScroll.addEventListener('scroll', () => {
    if (selectGuard) return;
    const visible = getVisibleEditorFile();
    const fileChanged = visible && visible !== activeFile;
    if (fileChanged) {
      activeFile = visible;
      renderTree();
      highlightActiveHeader();
    }
    if (syncLinked && syncSource !== 'preview' && !fileChanged && !editorSyncPending) {
      editorSyncPending = true;
      requestAnimationFrame(() => { syncEditorToPreview(); editorSyncPending = false; });
    }
  });
});

function findNode(nodes, path) {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (n.type === 'dir' && n.children) {
      const found = findNode(n.children, path);
      if (found) return found;
    }
  }
  return null;
}

// ── Preview ──────────────────────────────────────────────────────────────────
let previewRafPending = false;

function renderPreviewLocal() {
  // Render preview directly from textarea content (no server round-trip)
  const container = document.getElementById('preview');
  const sections = document.querySelectorAll('.file-section');
  let html = '';
  let sceneNum = 0;
  for (const section of sections) {
    const path = section.dataset.path;
    const ta = section.querySelector('textarea');
    if (!ta) continue;
    const slug = path.replace(/[\/\\.]/g, '-');
    html += '<span class="chapter-anchor" id="ch-'+esc(slug)+'"></span>';
    const fname = path.split('/').pop();
    const isScene = fname !== '_part.md' && !fname.startsWith('intermezzo-') && fname !== 'title.md';
    let content = ta.value;
    if (isScene) {
      sceneNum++;
      content = content.replace(/^(### )(.+)$/m, '$1' + sceneNum + '. $2');
    }
    html += md(content);
  }
  container.innerHTML = html;
}

function schedulePreviewUpdate() {
  if (previewRafPending) return;
  previewRafPending = true;
  requestAnimationFrame(() => {
    renderPreviewLocal();
    previewRafPending = false;
  });
}

async function renderPreview() {
  const data = await api('/api/preview');
  const container = document.getElementById('preview');
  let html = '';
  let sceneNum = 0;
  for (const f of data.files) {
    const slug = f.path.replace(/[\/\\.]/g, '-');
    html += '<span class="chapter-anchor" id="ch-'+esc(slug)+'"></span>';
    const fname = f.path.split('/').pop();
    const isScene = fname !== '_part.md' && !fname.startsWith('intermezzo-') && fname !== 'title.md';
    let content = f.content;
    if (isScene) {
      sceneNum++;
      // Inject number into first ### heading
      content = content.replace(/^(### )(.+)$/m, '$1' + sceneNum + '. $2');
    }
    html += md(content);
  }
  container.innerHTML = html;
}

// ── Git panel ────────────────────────────────────────────────────────────────

function switchPanel(panel) {
  currentPanel = panel;
  document.getElementById('tree').style.display = panel === 'files' ? '' : 'none';
  document.getElementById('gitPanel').style.display = panel === 'git' ? '' : 'none';
  document.querySelectorAll('.sidebar-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.panel === panel);
  });
  if (panel === 'git') refreshGit();
}

async function refreshGit() {
  try {
    gitState = await api('/api/git/status');
  } catch(e) {
    gitState = { git_installed: false, is_repo: false, is_temp: false, files: [] };
  }
  try {
    const logData = await api('/api/git/log');
    gitLog = logData.commits || [];
  } catch(e) {
    gitLog = [];
  }
  renderGitPanel();
  updateGitBadge();
}

function updateGitBadge() {
  const badge = document.getElementById('gitBadge');
  if (!gitState) { badge.style.display = 'none'; return; }
  const count = gitState.files.length;
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function renderGitPanel() {
  const el = document.getElementById('gitContent');
  if (!gitState) { el.innerHTML = ''; return; }

  if (!gitState.git_installed) {
    el.innerHTML = '<div class="git-message">Git is not available on this system.<br><br>Install Git to enable version control.</div>';
    return;
  }
  if (!gitState.is_repo) {
    if (gitState.is_temp) {
      el.innerHTML = '<div class="git-message">Git is not available for uploaded .bacalhau projects.<br><br>Save as a project directory to use Git.</div>';
    } else {
      el.innerHTML = '<div class="git-message">No repository found.<br><br><button class="primary" onclick="gitInit()" style="width:auto;padding:8px 20px;">Initialize Repository</button></div>';
    }
    return;
  }

  const staged = gitState.files.filter(f => f.staged);
  const unstaged = gitState.files.filter(f => !f.staged);
  let html = '';

  // Staged changes
  html += '<div class="git-section-header"><span>Staged Changes (' + staged.length + ')</span>';
  if (staged.length > 0) html += '<button onclick="gitUnstage()">Unstage All</button>';
  html += '</div>';
  for (const f of staged) {
    const badgeClass = f.status === '?' ? 'Q' : f.status;
    const display = f.path.split('/').pop();
    html += '<div class="git-file">';
    html += '<span class="git-badge ' + esc(badgeClass) + '">' + esc(f.status) + '</span>';
    html += '<span class="git-path" title="' + esc(f.path) + '">' + esc(display) + '</span>';
    html += '<button class="git-action" onclick="gitUnstage(\\'' + esc(f.path).replace(/'/g, "\\\\'") + '\\')" title="Unstage">\\u2212</button>';
    html += '</div>';
  }

  // Unstaged changes
  html += '<div class="git-section-header"><span>Changes (' + unstaged.length + ')</span>';
  if (unstaged.length > 0) html += '<button onclick="gitStage()">Stage All</button>';
  html += '</div>';
  for (const f of unstaged) {
    const badgeClass = f.status === '?' ? 'Q' : f.status;
    const display = f.path.split('/').pop();
    html += '<div class="git-file">';
    html += '<span class="git-badge ' + esc(badgeClass) + '">' + esc(f.status) + '</span>';
    html += '<span class="git-path" title="' + esc(f.path) + '">' + esc(display) + '</span>';
    html += '<button class="git-action" onclick="gitStage(\\'' + esc(f.path).replace(/'/g, "\\\\'") + '\\')" title="Stage">+</button>';
    html += '</div>';
  }

  // Commit area
  html += '<div class="git-commit-area">';
  html += '<input type="text" id="gitCommitMsg" placeholder="Commit message" onkeydown="if(event.key===\\'Enter\\')gitCommit()">';
  html += '<button class="primary" onclick="gitCommit()">Commit</button>';
  html += '</div>';

  // History
  if (gitLog.length > 0) {
    html += '<div class="git-history">';
    html += '<div class="git-section-header"><span>History</span></div>';
    for (const c of gitLog) {
      html += '<div class="git-commit-item">';
      html += '<div class="git-commit-msg">' + esc(c.message) + '</div>';
      html += '<div class="git-commit-meta">';
      html += '<span class="git-commit-when">' + esc(c.when) + '</span>';
      html += '<button class="git-action" onclick="gitRestore(\\'' + esc(c.sha) + '\\')" title="Restore to this version">restore</button>';
      html += '</div>';
      html += '</div>';
    }
    html += '</div>';
  }

  el.innerHTML = html;
}

async function gitRestore(sha) {
  if (!confirm('Restore your manuscript to this version? Your current text will be saved as a new checkpoint first.')) return;
  // Save any dirty files first
  await saveFile();
  // Stage and commit current state if there are changes
  const status = await api('/api/git/status');
  if (status.files && status.files.length > 0) {
    await api('/api/git/stage', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({all: true})});
    await api('/api/git/commit', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: 'Auto-save before restore'})});
  }
  const r = await api('/api/git/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sha})});
  if (r.error) { setStatus(r.error); return; }
  setStatus(r.message || 'Restored');
  await loadTree();
}

async function gitInit() {
  setStatus('Initializing repository...');
  const r = await api('/api/git/init', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  if (r.error) { setStatus(r.error); return; }
  setStatus('Repository initialized');
  refreshGit();
}

async function gitStage(path) {
  const body = path ? {path} : {all: true};
  await api('/api/git/stage', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  refreshGit();
}

async function gitUnstage(path) {
  const body = path ? {path} : {all: true};
  await api('/api/git/unstage', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  refreshGit();
}

async function gitCommit() {
  const input = document.getElementById('gitCommitMsg');
  const msg = input.value.trim();
  if (!msg) { setStatus('Commit message required'); return; }
  // Auto-stage all changes so writers don't have to think about staging
  await api('/api/git/stage', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({all: true})});
  const r = await api('/api/git/commit', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})});
  if (r.error) { setStatus(r.error); return; }
  input.value = '';
  setStatus('Committed ' + (r.sha || ''));
  refreshGit();
}

// ── File operations ──────────────────────────────────────────────────────────
async function newFile(dir, position) {
  // Create with default name, then inline rename
  const body = {slug: 'untitled', dir, autoIncrement: true};
  if (position !== undefined) body.position = position;
  const data = await api('/api/chapter/new', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  if (data.error) { setStatus(data.error); return; }
  await loadTree();
  // Find the new item's row and start inline rename
  if (data.path) {
    activeFile = data.path;
    renderTree();
    const fname = data.fname || 'untitled.md';
    setTimeout(() => {
      const row = document.querySelector('[data-path="'+data.path+'"]');
      if (row) startInlineRename(row, data.path, fname, 'file');
    }, 50);
  }
}

async function newDir(parentDir, position) {
  const body = {name: 'untitled', dir: parentDir, autoIncrement: true};
  if (position !== undefined) body.position = position;
  const data = await api('/api/dir/new', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  if (data.error) { setStatus(data.error); return; }
  await loadTree();
  // Find the new dir's row and start inline rename
  const actualName = data.name || 'untitled';
  const newPath = (parentDir ? parentDir + '/' : '') + actualName;
  setTimeout(() => {
    const row = document.querySelector('[data-path="'+newPath+'"]');
    if (row) startInlineRename(row, newPath, actualName, 'dir');
  }, 50);
}

function newFileAt(parentPath, position) { newFile(parentPath, position); }
function newDirAt(parentPath, position) { newDir(parentPath, position); }

let renaming = false;

function startInlineRename(row, path, currentName, type) {
  renaming = true;
  const label = row.querySelector('.label');
  const baseName = type === 'file' ? currentName.replace(/\\.md$/, '') : currentName;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = baseName;
  input.style.cssText = 'width:100%;font-size:12px;background:var(--bg);color:var(--fg);border:1px solid var(--accent);border-radius:2px;padding:1px 4px;outline:none;font-family:inherit;';
  label.textContent = '';
  label.appendChild(input);
  input.focus();
  input.select();

  let done = false;
  function commit() {
    if (done) return;
    done = true;
    renaming = false;
    const newName = input.value.trim();
    if (!newName || newName === baseName) { renderTree(); return; }
    renameItem(path, newName, type);
  }
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { done = true; renaming = false; renderTree(); }
    e.stopPropagation();
  });
  input.addEventListener('blur', commit);
  input.addEventListener('mousedown', e => e.stopPropagation());
}

async function renameItem(path, newName, type) {
  const data = await api('/api/rename', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({path, newName, type})
  });
  if (data.error) { setStatus(data.error); renderTree(); return; }
  setStatus(data.message);
  // Update activeFile if it was renamed
  if (activeFile === path && data.newPath) {
    activeFile = data.newPath;
  }
  await loadTree();
}

async function copyFile(path) {
  setStatus('Copying...');
  const data = await api('/api/chapter/' + encodeURI(path) + '/copy', {method:'POST'});
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  await loadTree();
}

async function removeFile(path) {
  if (!confirm('Delete ' + path + '?')) return;
  const data = await api('/api/chapter/' + encodeURI(path), {method:'DELETE'});
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  if (activeFile === path) { activeFile = null; }
  await loadTree();
}

async function copyDir(path) {
  setStatus('Copying folder...');
  const data = await api('/api/dir/' + encodeURI(path) + '/copy', {method:'POST'});
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  await loadTree();
}

async function removeDir(path) {
  if (!confirm('Delete folder ' + path + ' and all contents?')) return;
  const data = await api('/api/dir/' + encodeURI(path), {method:'DELETE'});
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  await loadTree();
}

async function toggleLock(path) {
  const data = await api('/api/chapter/' + encodeURI(path) + '/chmod', {method:'POST'});
  if (data.error) { setStatus(data.error); return; }
  setStatus(data.message);
  await loadTree();
}

function handleSaveAs(fmt) {
  if (fmt === 'bacalhau') saveBacalhau();
  else if (fmt === 'zip') saveProject();
  else if (fmt === 'md') exportMarkdown();
  else if (fmt === 'pdf') exportPDF();
}

async function saveBacalhau() {
  setStatus('Saving .bacalhau\u2026');
  try {
    const r = await fetch('/api/save/bacalhau');
    if (!r.ok) {
      const err = await r.json();
      setStatus(err.error || 'Save failed');
      return;
    }
    const ct = r.headers.get('Content-Type') || '';
    if (ct.includes('application/json')) {
      const data = await r.json();
      setStatus('Saved to ' + (data.path || '.bacalhau'));
    } else {
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const disp = r.headers.get('Content-Disposition') || '';
      const match = disp.match(/filename="?([^"]+)"?/);
      a.download = match ? match[1] : 'project.bacalhau';
      a.click();
      URL.revokeObjectURL(url);
      setStatus('Downloaded ' + a.download);
    }
  } catch(e) {
    setStatus('Save failed');
  }
}

async function handleOpenFile(input) {
  const file = input.files[0];
  if (!file) return;
  setStatus('Opening ' + file.name + '\u2026');
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);
    const r = await fetch('/api/open', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: file.name, data: b64})
    });
    const data = await r.json();
    if (data.error) { setStatus(data.error); return; }
    input.value = '';
    document.getElementById('welcomeOverlay').style.display = 'none';
    await loadTree();
    setStatus('Opened ' + file.name);
  } catch(e) {
    setStatus('Open failed');
  }
}

// ── Folder browser ───────────────────────────────────────────────────────────
let browsePath = null;
let browseData = null;

async function openBrowse() {
  document.getElementById('browseOverlay').style.display = 'flex';
  await browseTo(null);
}

function closeBrowse() {
  document.getElementById('browseOverlay').style.display = 'none';
}

async function browseTo(path) {
  const url = path ? '/api/browse?path=' + encodeURIComponent(path) : '/api/browse';
  try {
    const data = await api(url);
    if (data.error) { setStatus(data.error); return; }
    browseData = data;
    browsePath = data.path;
    renderBrowse();
  } catch(e) {
    setStatus('Browse failed');
  }
}

function renderBrowse() {
  // Breadcrumb
  const bc = document.getElementById('browseBreadcrumb');
  const homePath = browseData.home || '';
  let html = '<span onclick="browseTo(null)">Home</span>';
  if (!browseData.atHome) {
    const rel = browseData.path.slice(homePath.length).replace(/^\\//, '');
    const segments = rel.split('/');
    for (let i = 0; i < segments.length; i++) {
      const segPath = homePath + '/' + segments.slice(0, i + 1).join('/');
      html += '<span class="sep"> / </span><span onclick="browseTo(\\'' + esc(segPath).replace(/'/g, "\\\\'") + '\\')">' + esc(segments[i]) + '</span>';
    }
  }
  bc.innerHTML = html;

  // Directory list
  const list = document.getElementById('browseList');
  html = '';

  // Parent directory link
  if (browseData.parent) {
    html += '<div class="browse-item" onclick="browseTo(\\'' + esc(browseData.parent).replace(/'/g, "\\\\'") + '\\')">';
    html += '<span class="browse-icon">\\u2190</span>';
    html += '<span class="browse-name" style="color:var(--fg2)">..</span>';
    html += '</div>';
  }

  if (browseData.entries.length === 0 && !browseData.parent) {
    html += '<div class="browse-empty">This folder is empty</div>';
  }

  for (const e of browseData.entries) {
    const cls = e.isProject ? 'browse-item is-project' : 'browse-item';
    const entryPath = browseData.path + '/' + e.name;
    html += '<div class="' + cls + '" onclick="browseTo(\\'' + esc(entryPath).replace(/'/g, "\\\\'") + '\\')">';
    html += '<span class="browse-icon">\\uD83D\\uDCC1</span>';
    html += '<span class="browse-name">' + esc(e.name) + '</span>';
    if (e.mdCount > 0) {
      html += '<span class="browse-hint">' + e.mdCount + ' .md</span>';
    }
    html += '</div>';
  }

  list.innerHTML = html;

  // Footer hint
  const hint = document.getElementById('browseFooterHint');
  if (browseData.mdCount > 0) {
    hint.textContent = browseData.mdCount + ' markdown file' + (browseData.mdCount === 1 ? '' : 's') + ' here';
  } else {
    hint.textContent = 'No markdown files here';
  }
}

async function browseOpenHere() {
  if (!browsePath) return;
  if (browseData && browseData.mdCount === 0 && !confirm('This folder has no markdown files. Open anyway?')) return;
  setStatus('Opening folder\\u2026');
  try {
    const r = await api('/api/open/folder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: browsePath})
    });
    if (r.error) { setStatus(r.error); return; }
    closeBrowse();
    document.getElementById('welcomeOverlay').style.display = 'none';
    await loadTree();
    setStatus('Opened ' + browsePath.split('/').pop());
  } catch(e) {
    setStatus('Open failed');
  }
}

async function saveProject() {
  setStatus('Saving...');
  const r = await fetch('/api/save/zip');
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bone-china-chapters.zip';
  a.click();
  URL.revokeObjectURL(url);
  setStatus('Downloaded bone-china-chapters.zip');
}

async function exportMarkdown() {
  setStatus('Generating...');
  const r = await fetch('/api/export/markdown');
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bone-china.md';
  a.click();
  URL.revokeObjectURL(url);
  setStatus('Downloaded bone-china.md');
}

async function exportPDF() {
  setStatus('Generating PDF\u2026');
  try {
    const r = await fetch('/api/export/pdf');
    if (!r.ok) {
      const err = await r.json();
      setStatus(err.error || 'PDF export failed');
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bone-china.pdf';
    a.click();
    URL.revokeObjectURL(url);
    setStatus('Downloaded bone-china.pdf');
  } catch(e) {
    setStatus('PDF export failed');
  }
}

// ── Sync ─────────────────────────────────────────────────────────────────────
let syncLinked = false;
let syncSource = null;

function getChapterRange(path) {
  const pane = document.getElementById('previewPane');
  const slug = path.replace(/[\\/\\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (!anchor) return null;
  // Find next anchor
  const allAnchors = pane.querySelectorAll('.chapter-anchor');
  let found = false;
  let nextTop = pane.scrollHeight;
  for (const a of allAnchors) {
    if (found) { nextTop = a.offsetTop; break; }
    if (a === anchor) found = true;
  }
  return {top: anchor.offsetTop, height: nextTop - anchor.offsetTop};
}

function syncEditorToPreview() {
  const editorScroll = document.getElementById('editorScroll');
  const pane = document.getElementById('previewPane');
  if (!editorScroll || !pane) return;

  // Find which file section is at the 30% viewport mark, and how far through it
  const viewY = editorScroll.scrollTop + editorScroll.clientHeight * 0.5;
  const sections = document.querySelectorAll('.file-section');
  let targetSection = null;
  let localRatio = 0;

  for (const s of sections) {
    const top = s.offsetTop;
    const bot = top + s.offsetHeight;
    if (viewY >= top && viewY < bot) {
      targetSection = s;
      localRatio = s.offsetHeight > 0 ? (viewY - top) / s.offsetHeight : 0;
      break;
    }
  }
  if (!targetSection) return;

  const path = targetSection.dataset.path;
  const slug = path.replace(/[\/\\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (!anchor) return;

  // Find this file's range in the preview
  const allAnchors = Array.from(pane.querySelectorAll('.chapter-anchor'));
  const idx = allAnchors.indexOf(anchor);
  const previewTop = anchor.offsetTop;
  const previewBot = idx + 1 < allAnchors.length ? allAnchors[idx + 1].offsetTop : pane.scrollHeight;
  const previewHeight = previewBot - previewTop;

  const target = previewTop + localRatio * previewHeight - pane.clientHeight * 0.5;
  syncSource = 'editor';
  pane.scrollTop = Math.max(0, target);
  setTimeout(() => { syncSource = null; }, 300);
}

function syncPreviewToEditor() {
  const editorScroll = document.getElementById('editorScroll');
  const pane = document.getElementById('previewPane');
  if (!editorScroll || !pane) return;

  // Find which preview chapter is at the 30% viewport mark
  const viewY = pane.scrollTop + pane.clientHeight * 0.5;
  const allAnchors = Array.from(pane.querySelectorAll('.chapter-anchor'));
  let anchorIdx = 0;
  for (let i = allAnchors.length - 1; i >= 0; i--) {
    if (allAnchors[i].offsetTop <= viewY) { anchorIdx = i; break; }
  }

  const anchor = allAnchors[anchorIdx];
  const previewTop = anchor.offsetTop;
  const previewBot = anchorIdx + 1 < allAnchors.length ? allAnchors[anchorIdx + 1].offsetTop : pane.scrollHeight;
  const previewHeight = previewBot - previewTop;
  const localRatio = previewHeight > 0 ? (viewY - previewTop) / previewHeight : 0;

  // Find the corresponding editor section
  const slug = anchor.id.replace('ch-', '');
  const section = Array.from(document.querySelectorAll('.file-section')).find(s =>
    s.dataset.path.replace(/[\/\\.]/g, '-') === slug
  );
  if (!section) return;

  const target = section.offsetTop + localRatio * section.offsetHeight - editorScroll.clientHeight * 0.5;
  syncSource = 'preview';
  editorScroll.scrollTop = Math.max(0, target);
  setTimeout(() => { syncSource = null; }, 300);
}

function getVisibleChapter() {
  const pane = document.getElementById('previewPane');
  const midY = pane.getBoundingClientRect().top + pane.clientHeight * 0.5;
  const anchors = pane.querySelectorAll('.chapter-anchor');
  let best = null, bestDist = Infinity;
  for (const a of anchors) {
    const d = Math.abs(a.getBoundingClientRect().top - midY);
    if (d < bestDist) { bestDist = d; best = a.id.replace('ch-', '').replace(/-/g, m => m); }
  }
  // Convert slug back to path — find in tree
  if (!best) return null;
  return findPathBySlug(tree, best);
}

function findPathBySlug(nodes, slug) {
  for (const n of nodes) {
    if (n.type === 'file' && n.path.replace(/[\\/\\.]/g, '-') === slug) return n.path;
    if (n.type === 'dir' && n.children) {
      const found = findPathBySlug(n.children, slug);
      if (found) return found;
    }
  }
  return null;
}

function toggleSync() {
  syncLinked = !syncLinked;
  document.getElementById('syncToggle').classList.toggle('active', syncLinked);
  document.getElementById('syncIcon').textContent = syncLinked ? '\\u{1F517}' : '\\u{26D3}';
  if (syncLinked) syncEditorToPreview();
}

// Editor scroll sync handled in the unified scroll listener above

let previewSyncPending = false;
document.getElementById('previewPane').addEventListener('scroll', () => {
  if (!syncLinked || syncSource === 'editor' || selectGuard) return;
  const visible = getVisibleChapter();
  const fileChanged = visible && visible !== activeFile;
  if (fileChanged) {
    activeFile = visible;
    renderTree();
    highlightActiveHeader();
  }
  if (!fileChanged && !previewSyncPending) {
    previewSyncPending = true;
    requestAnimationFrame(() => { syncPreviewToEditor(); previewSyncPending = false; });
  }
});

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function setStatus(msg) { document.getElementById('status').textContent = msg; }

function countWords(text) {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\\s+/).length;
}

function updateWordCount() {
  const el = document.getElementById('wordCount');
  if (!el) return;
  let fileWords = 0;
  if (activeFile) {
    const ta = document.querySelector('.file-section[data-path="' + activeFile + '"] textarea');
    if (ta) fileWords = countWords(ta.value);
  }
  let totalWords = 0;
  document.querySelectorAll('.file-section textarea').forEach(ta => {
    totalWords += countWords(ta.value);
  });
  const parts = [];
  if (activeFile) parts.push('File: ' + fileWords.toLocaleString());
  parts.push('Total: ' + totalWords.toLocaleString());
  el.textContent = parts.join(' \\u2022 ');
}

document.addEventListener('keydown', e => {
  if ((e.metaKey||e.ctrlKey) && e.key === 's') { e.preventDefault(); saveFile(); }
  if ((e.metaKey||e.ctrlKey) && e.key === 'o') { e.preventDefault(); document.getElementById('openInput').click(); }
});

// ── Themes ───────────────────────────────────────────────────────────────────
async function loadThemes() {
  const data = await api('/api/themes');
  const select = document.getElementById('themeSelect');
  select.innerHTML = '<option value="">No theme</option>';
  for (const name of data.themes) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name.replace('.css', '');
    select.appendChild(opt);
  }
  const sep = document.createElement('option');
  sep.disabled = true;
  sep.textContent = '\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500';
  select.appendChild(sep);
  const imp = document.createElement('option');
  imp.value = '__import__';
  imp.textContent = 'Import theme\u2026';
  select.appendChild(imp);
  // Restore saved theme
  const saved = localStorage.getItem('bc-theme') || '';
  if (saved && data.themes.includes(saved)) {
    select.value = saved;
    applyTheme(saved);
  }
}

function switchTheme(name) {
  if (name === '__import__') {
    document.getElementById('themeInput').click();
    // Reset select to current theme
    const select = document.getElementById('themeSelect');
    select.value = localStorage.getItem('bc-theme') || '';
    return;
  }
  localStorage.setItem('bc-theme', name);
  applyTheme(name);
}

async function handleImportTheme(input) {
  const file = input.files[0];
  if (!file) return;
  setStatus('Importing theme\u2026');
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);
    const r = await fetch('/api/themes/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: file.name, data: b64})
    });
    const data = await r.json();
    if (data.error) { setStatus(data.error); return; }
    input.value = '';
    await loadThemes();
    // Auto-select the imported theme
    document.getElementById('themeSelect').value = file.name;
    switchTheme(file.name);
    setStatus('Imported ' + file.name);
  } catch(e) {
    setStatus('Import failed');
  }
}

function applyTheme(name) {
  const link = document.getElementById('theme-css');
  link.href = name ? '/api/themes/' + encodeURIComponent(name) : '';
}

loadTree();
loadThemes();

// Heartbeat — tells server we're alive
let serverDead = false;
setInterval(() => {
  fetch('/api/heartbeat').then(r => {
    if (serverDead) {
      serverDead = false;
      const s = document.getElementById('status');
      s.textContent = 'Reconnected';
      s.style.color = '';
      s.style.fontWeight = '';
      setTimeout(() => { if (s.textContent === 'Reconnected') s.textContent = ''; }, 3000);
    }
  }).catch(() => {
    serverDead = true;
    const s = document.getElementById('status');
    s.textContent = 'Server disconnected';
    s.style.color = '#e55';
    s.style.fontWeight = '700';
  });
}, 10000);
// Re-ping immediately when tab becomes visible (recovers from App Nap / background throttling)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) fetch('/api/heartbeat').catch(()=>{});
});
// Beacon on close — immediate shutdown signal
window.addEventListener('beforeunload', () => {
  navigator.sendBeacon('/api/shutdown');
});
</script>
</body>
</html>
"""


# ── Server ────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.serve_html()
        elif self.path == "/favicon.png":
            self.serve_favicon()
        elif self.path == "/api/tree":
            self.serve_tree()
        elif self.path.startswith("/api/chapter/"):
            self.serve_chapter()
        elif self.path == "/api/preview":
            self.serve_preview()
        elif self.path == "/api/export/markdown":
            self.export_markdown()
        elif self.path == "/api/export/pdf":
            self.export_pdf()
        elif self.path == "/api/save/zip":
            self.save_zip()
        elif self.path == "/api/save/bacalhau":
            self.save_bacalhau()
        elif self.path == "/api/themes":
            self.serve_themes_list()
        elif self.path == "/api/heartbeat":
            global _last_heartbeat
            _last_heartbeat = time.time()
            self.send_json(200, {"ok": True})
        elif self.path.startswith("/api/themes/"):
            self.serve_theme_css()
        elif self.path == "/api/git/status":
            self.git_status()
        elif self.path == "/api/git/log":
            self.git_log()
        elif self.path.startswith("/api/browse"):
            self.browse_directory()
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/tree/move":
            self.move_item()
        elif self.path == "/api/rename":
            self.rename_item()
        elif self.path == "/api/chapter/new":
            self.new_chapter()
        elif self.path == "/api/dir/new":
            self.new_dir()
        elif self.path.startswith("/api/dir/") and self.path.endswith("/copy"):
            self.copy_dir()
        elif self.path.endswith("/copy"):
            self.copy_chapter()
        elif self.path.endswith("/chmod"):
            self.chmod_chapter()
        elif self.path == "/api/shutdown":
            _repack_bacalhau()
            self.send_json(200, {"ok": True})
            threading.Timer(0.5, lambda: os._exit(0)).start()
        elif self.path == "/api/open":
            self.open_project()
        elif self.path == "/api/themes/import":
            self.import_theme()
        elif self.path == "/api/git/init":
            self.git_init()
        elif self.path == "/api/git/stage":
            self.git_stage()
        elif self.path == "/api/git/unstage":
            self.git_unstage()
        elif self.path == "/api/git/commit":
            self.git_commit()
        elif self.path == "/api/git/restore":
            self.git_restore()
        elif self.path == "/api/open/folder":
            self.open_folder()
        else:
            self.send_json(404, {"error": "Not found"})

    def do_PUT(self):
        if self.path.startswith("/api/chapter/"):
            self.save_chapter()
        else:
            self.send_json(404, {"error": "Not found"})

    def do_DELETE(self):
        if self.path.startswith("/api/dir/"):
            self.delete_dir()
        elif self.path.startswith("/api/chapter/"):
            self.delete_chapter()
        else:
            self.send_json(404, {"error": "Not found"})

    # ── Routes ────────────────────────────────────────────────────────────────

    def serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def serve_favicon(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon = os.path.join(script_dir, "icons", "icon.png")
        if not os.path.isfile(icon):
            icon = os.path.join(script_dir, "icon.png")
        if os.path.exists(icon):
            with open(icon, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def serve_tree(self):
        t = build_tree(CHAPTERS_DIR)
        self.send_json(200, {"tree": t})

    def serve_chapter(self):
        relpath = self.path.split("/api/chapter/", 1)[1]
        try:
            abspath = resolve_path(relpath)
            with open(abspath, "r") as f:
                content = f.read()
            self.send_json(200, {"content": content})
        except (FileNotFoundError, ValueError) as e:
            self.send_json(404, {"error": str(e)})

    def save_chapter(self):
        relpath = self.path.split("/api/chapter/", 1)[1]
        try:
            abspath = resolve_path(relpath)
        except ValueError as e:
            self.send_json(400, {"error": str(e)})
            return
        if os.path.exists(abspath) and not os.access(abspath, os.W_OK):
            self.send_json(403, {"error": "File is read-only"})
            return
        body = self.read_body()
        with open(abspath, "w") as f:
            f.write(body.get("content", ""))
        self.send_json(200, {"message": "Saved"})

    def serve_preview(self):
        files = []
        for filepath in walk_files(CHAPTERS_DIR):
            relpath = os.path.relpath(filepath, CHAPTERS_DIR)
            try:
                with open(filepath, "r") as f:
                    content = f.read()
            except FileNotFoundError:
                content = ""
            files.append({"path": relpath, "content": content})
        self.send_json(200, {"files": files})

    def rename_item(self):
        body = self.read_body()
        old_path = body.get("path", "")
        new_name = body.get("newName", "").strip()
        item_type = body.get("type", "file")

        if not new_name:
            self.send_json(400, {"error": "Name is required"})
            return

        # Sanitize
        new_name = re.sub(r"[^a-zA-Z0-9_. -]", "-", new_name).strip("-")
        if not new_name:
            self.send_json(400, {"error": "Invalid name"})
            return

        old_abs = resolve_path(old_path)
        parent_dir = os.path.dirname(old_abs)
        old_name = os.path.basename(old_abs)

        if item_type == "file":
            if not new_name.endswith(".md"):
                new_name += ".md"
            new_abs = os.path.join(parent_dir, new_name)
        else:
            new_abs = os.path.join(parent_dir, new_name)

        if os.path.exists(new_abs):
            self.send_json(409, {"error": f"Already exists: {new_name}"})
            return

        # Update _order.yaml BEFORE renaming on disk
        # (otherwise read_order sees the new name on disk and appends a duplicate)
        old_entry = old_name + "/" if item_type == "dir" else old_name
        new_entry = new_name + "/" if item_type == "dir" else new_name
        order_file = os.path.join(parent_dir, "_order.yaml")
        if os.path.exists(order_file):
            raw = []
            with open(order_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("- "):
                        raw.append(line[2:].strip())
            if old_entry in raw:
                raw[raw.index(old_entry)] = new_entry
            write_order(parent_dir, raw)

        os.rename(old_abs, new_abs)

        # Update the ### heading inside the file to match the new name
        if item_type == "file" and os.path.exists(new_abs):
            title = new_name.replace(".md", "").replace("-", " ").title()
            with open(new_abs, "r") as f:
                content = f.read()
            content = re.sub(r"^(### ).+$", rf"\g<1>{title}", content, count=1, flags=re.MULTILINE)
            with open(new_abs, "w") as f:
                f.write(content)

        new_relpath = os.path.relpath(new_abs, CHAPTERS_DIR)
        self.send_json(200, {
            "message": f"Renamed → {new_name}",
            "newPath": new_relpath
        })

    def new_chapter(self):
        body = self.read_body()
        slug = re.sub(r"[^a-z0-9-]", "-", body.get("slug", "").lower()).strip("-")
        parent_dir = body.get("dir", "")
        auto = body.get("autoIncrement", False)
        if not slug:
            self.send_json(400, {"error": "Invalid slug"})
            return
        dirpath = resolve_path(parent_dir) if parent_dir else CHAPTERS_DIR
        fname = slug + ".md"
        filepath = os.path.join(dirpath, fname)
        if os.path.exists(filepath):
            if auto:
                n = 2
                while os.path.exists(os.path.join(dirpath, f"{slug}-{n}.md")):
                    n += 1
                slug = f"{slug}-{n}"
                fname = slug + ".md"
                filepath = os.path.join(dirpath, fname)
            else:
                self.send_json(409, {"error": f"Already exists: {fname}"})
                return
        title = slug.replace("-", " ").title()
        # Update _order.yaml BEFORE creating the file on disk
        # (otherwise read_order's append-unlisted logic would add a duplicate)
        position = body.get("position")
        order = _read_order_raw(dirpath)
        if position is not None and 0 <= position <= len(order):
            order.insert(position, fname)
        else:
            order.append(fname)
        write_order(dirpath, order)
        with open(filepath, "w") as f:
            f.write(f"### {title}\n\n")
        relpath = os.path.relpath(filepath, CHAPTERS_DIR)
        self.send_json(200, {"path": relpath, "fname": fname, "message": f"Created {relpath}"})

    def new_dir(self):
        body = self.read_body()
        name = re.sub(r"[^a-z0-9-]", "-", body.get("name", "").lower()).strip("-")
        parent_dir = body.get("dir", "")
        auto = body.get("autoIncrement", False)
        if not name:
            self.send_json(400, {"error": "Invalid name"})
            return
        dirpath = resolve_path(parent_dir) if parent_dir else CHAPTERS_DIR
        new_dirpath = os.path.join(dirpath, name)
        if os.path.exists(new_dirpath):
            if auto:
                n = 2
                while os.path.exists(os.path.join(dirpath, f"{name}-{n}")):
                    n += 1
                name = f"{name}-{n}"
                new_dirpath = os.path.join(dirpath, name)
            else:
                self.send_json(409, {"error": f"Already exists: {name}"})
                return
        # Update parent _order.yaml BEFORE creating the dir on disk
        position = body.get("position")
        order = _read_order_raw(dirpath)
        entry = name + "/"
        if position is not None and 0 <= position <= len(order):
            order.insert(position, entry)
        else:
            order.append(entry)
        write_order(dirpath, order)
        os.makedirs(new_dirpath)
        write_order(new_dirpath, [])
        self.send_json(200, {"name": name, "message": f"Created {name}/"})

    def move_item(self):
        body = self.read_body()
        src = body.get("src", "")
        src_type = body.get("src_type", "file")
        dest_dir = body.get("dest_dir", "")
        position = body.get("position", -1)

        src_abs = resolve_path(src)
        src_name = os.path.basename(src_abs)
        src_parent = os.path.dirname(src_abs)
        dest_abs = resolve_path(dest_dir) if dest_dir else CHAPTERS_DIR

        if not os.path.exists(src_abs):
            self.send_json(404, {"error": f"Not found: {src}"})
            return

        entry_name = src_name + "/" if src_type == "dir" else src_name
        same_dir = os.path.normpath(src_parent) == os.path.normpath(dest_abs)

        if same_dir:
            # Reorder within the same directory
            order = read_order(src_parent)
            if entry_name not in order:
                self.send_json(400, {"error": f"Not in order: {entry_name}"})
                return
            old_idx = order.index(entry_name)
            order.pop(old_idx)
            # Adjust position if the item was before the target
            insert_pos = position
            if position < 0 or position >= len(order):
                insert_pos = len(order)
            elif old_idx < position:
                insert_pos = position - 1
            order.insert(insert_pos, entry_name)
            write_order(src_parent, order)
        else:
            # Move between directories
            # Remove from source
            src_order = read_order(src_parent)
            if entry_name in src_order:
                src_order.remove(entry_name)
                write_order(src_parent, src_order)

            # Move on disk
            new_path = os.path.join(dest_abs, src_name)
            shutil.move(src_abs, new_path)

            # Add to dest
            dest_order = read_order(dest_abs)
            if entry_name in dest_order:
                dest_order.remove(entry_name)
            if position < 0 or position >= len(dest_order):
                dest_order.append(entry_name)
            else:
                dest_order.insert(position, entry_name)
            write_order(dest_abs, dest_order)

        self.send_json(200, {"message": f"Moved {src_name}"})

    def copy_chapter(self):
        relpath = self.path.split("/api/chapter/", 1)[1].replace("/copy", "")
        abspath = resolve_path(relpath)
        if not os.path.exists(abspath):
            self.send_json(404, {"error": "Not found"})
            return
        dirname = os.path.dirname(abspath)
        basename = os.path.basename(abspath).replace(".md", "")
        n = 1
        while True:
            suffix = "-copy" if n == 1 else f"-copy-{n}"
            new_name = f"{basename}{suffix}.md"
            if not os.path.exists(os.path.join(dirname, new_name)):
                break
            n += 1
        # Update _order.yaml BEFORE copying on disk
        order = _read_order_raw(dirname)
        orig_name = os.path.basename(abspath)
        idx = order.index(orig_name) + 1 if orig_name in order else len(order)
        order.insert(idx, new_name)
        write_order(dirname, order)
        shutil.copy2(abspath, os.path.join(dirname, new_name))
        self.send_json(200, {"message": f"Copied to {new_name}"})

    def delete_chapter(self):
        relpath = self.path.split("/api/chapter/", 1)[1]
        abspath = resolve_path(relpath)
        if not os.path.exists(abspath):
            self.send_json(404, {"error": "Not found"})
            return
        dirname = os.path.dirname(abspath)
        fname = os.path.basename(abspath)
        os.remove(abspath)
        order = read_order(dirname)
        if fname in order:
            order.remove(fname)
            write_order(dirname, order)
        self.send_json(200, {"message": f"Deleted {relpath}"})

    def copy_dir(self):
        relpath = self.path.split("/api/dir/", 1)[1].replace("/copy", "")
        abspath = resolve_path(relpath)
        if not os.path.isdir(abspath):
            self.send_json(404, {"error": "Not found"})
            return
        parent = os.path.dirname(abspath)
        basename = os.path.basename(abspath)
        # Generate copy name
        n = 1
        while True:
            suffix = "-copy" if n == 1 else f"-copy-{n}"
            new_name = f"{basename}{suffix}"
            if not os.path.exists(os.path.join(parent, new_name)):
                break
            n += 1
        # Update _order.yaml BEFORE copying on disk
        order = _read_order_raw(parent)
        entry = basename + "/"
        new_entry = new_name + "/"
        idx = order.index(entry) + 1 if entry in order else len(order)
        order.insert(idx, new_entry)
        write_order(parent, order)
        shutil.copytree(abspath, os.path.join(parent, new_name))
        self.send_json(200, {"message": f"Copied to {new_name}/"})

    def delete_dir(self):
        relpath = self.path.split("/api/dir/", 1)[1]
        abspath = resolve_path(relpath)
        if not os.path.isdir(abspath):
            self.send_json(404, {"error": "Not found"})
            return
        parent = os.path.dirname(abspath)
        dirname = os.path.basename(abspath)
        shutil.rmtree(abspath)
        order = read_order(parent)
        entry = dirname + "/"
        if entry in order:
            order.remove(entry)
            write_order(parent, order)
        self.send_json(200, {"message": f"Deleted {relpath}/"})

    def chmod_chapter(self):
        relpath = self.path.split("/api/chapter/", 1)[1].replace("/chmod", "")
        abspath = resolve_path(relpath)
        if not os.path.exists(abspath):
            self.send_json(404, {"error": "Not found"})
            return
        current = os.stat(abspath).st_mode
        is_writable = bool(current & stat.S_IWUSR)
        if is_writable:
            os.chmod(abspath, current & ~stat.S_IWUSR)
            self.send_json(200, {"writable": False, "message": f"{relpath} → read-only"})
        else:
            os.chmod(abspath, current | stat.S_IWUSR)
            self.send_json(200, {"writable": True, "message": f"{relpath} → writable"})

    def export_markdown(self):
        """Concatenate chapters with scene numbers and serve as a download."""
        parts = []
        counter = [0]
        for filepath in walk_files(CHAPTERS_DIR):
            with open(filepath, "r") as f:
                content = f.read()
            basename = os.path.basename(filepath)
            if basename != "_part.md" and not basename.startswith("intermezzo-") and basename != "title.md":
                counter[0] += 1
                n = counter[0]
                content = re.sub(r"^(### )(.+)$", rf"\g<1>{n}. \2", content, count=1, flags=re.MULTILINE)
            parts.append(content)
        text = "".join(parts)
        encoded = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="bone-china.md"')
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def export_pdf(self):
        """Generate PDF from assembled markdown (pure Python, no external deps)."""
        # Assemble markdown with scene numbers
        parts = []
        counter = [0]
        for filepath in walk_files(CHAPTERS_DIR):
            with open(filepath, "r") as f:
                content = f.read()
            basename = os.path.basename(filepath)
            if basename != "_part.md" and not basename.startswith("intermezzo-") and basename != "title.md":
                counter[0] += 1
                n = counter[0]
                content = re.sub(r"^(### )(.+)$", rf"\g<1>{n}. \2", content, count=1, flags=re.MULTILINE)
            parts.append(content)
        text = "".join(parts)
        try:
            vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
            if vendor_dir not in sys.path:
                sys.path.insert(0, vendor_dir)
            from md2pdf import markdown_to_pdf_bytes
            data = markdown_to_pdf_bytes(text)
        except Exception as e:
            self.send_json(500, {"error": f"PDF generation failed: {str(e)[:500]}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="bone-china.pdf"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def save_zip(self):
        """Zip the entire chapters/ directory and serve as a download."""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(CHAPTERS_DIR):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    filepath = os.path.join(root, fname)
                    arcname = os.path.relpath(filepath, CHAPTERS_DIR)
                    zf.write(filepath, os.path.join("chapters", arcname))
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="bone-china-chapters.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def save_bacalhau(self):
        """Save project as .bacalhau (zip with custom extension)."""
        import io
        import zipfile
        if BACALHAU_FILE:
            # In-place save: repack to original file
            _repack_bacalhau()
            self.send_json(200, {"message": "Saved", "path": os.path.basename(BACALHAU_FILE)})
        else:
            # Download mode: build zip and serve
            buf = io.BytesIO()
            project_root = os.path.dirname(CHAPTERS_DIR)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(CHAPTERS_DIR):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fname in sorted(files):
                        if fname.startswith("."):
                            continue
                        filepath = os.path.join(root, fname)
                        arcname = os.path.relpath(filepath, CHAPTERS_DIR)
                        zf.write(filepath, os.path.join("chapters", arcname))
                latex_dir = os.path.join(project_root, "latex")
                if os.path.isdir(latex_dir):
                    for root2, dirs2, files2 in os.walk(latex_dir):
                        dirs2[:] = [d for d in dirs2 if not d.startswith(".")]
                        for fname in sorted(files2):
                            if fname.startswith("."):
                                continue
                            filepath = os.path.join(root2, fname)
                            arcname = os.path.relpath(filepath, latex_dir)
                            zf.write(filepath, os.path.join("latex", arcname))
            data = buf.getvalue()
            name = os.path.basename(project_root) + ".bacalhau"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def open_project(self):
        """Open a .bacalhau file uploaded from the browser."""
        global CHAPTERS_DIR, BACALHAU_FILE, TEMP_DIR
        import base64
        import tempfile
        import zipfile
        body = self.read_body()
        if not body or "data" not in body:
            self.send_json(400, {"error": "Missing file data"})
            return
        try:
            raw = base64.b64decode(body["data"])
        except Exception:
            self.send_json(400, {"error": "Invalid file data"})
            return
        # Save current project if it's a .bacalhau
        _repack_bacalhau()
        # Clean up old temp dir
        old_temp = TEMP_DIR
        # Extract to new temp dir
        new_temp = tempfile.mkdtemp(prefix="bacalhau-")
        tmp_file = os.path.join(new_temp, "upload.bacalhau")
        with open(tmp_file, "wb") as f:
            f.write(raw)
        try:
            with zipfile.ZipFile(tmp_file, "r") as zf:
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(new_temp, member))
                    if not target.startswith(os.path.realpath(new_temp) + os.sep) and target != os.path.realpath(new_temp):
                        self.send_json(400, {"error": f"Unsafe path in archive: {member}"})
                        shutil.rmtree(new_temp, ignore_errors=True)
                        return
                zf.extractall(new_temp)
            os.unlink(tmp_file)
        except zipfile.BadZipFile:
            self.send_json(400, {"error": "Not a valid .bacalhau file"})
            shutil.rmtree(new_temp, ignore_errors=True)
            return
        chapters_path = os.path.join(new_temp, "chapters")
        if not os.path.isdir(chapters_path):
            self.send_json(400, {"error": "No chapters/ directory in file"})
            shutil.rmtree(new_temp, ignore_errors=True)
            return
        # Switch to new project
        CHAPTERS_DIR = chapters_path
        BACALHAU_FILE = None  # Uploaded copy — no disk path
        TEMP_DIR = new_temp
        # Clean up old temp
        if old_temp and os.path.isdir(old_temp):
            shutil.rmtree(old_temp, ignore_errors=True)
        self.send_json(200, {"ok": True, "name": body.get("filename", "project.bacalhau")})

    # ── Folder browser ──────────────────────────────────────────────────────────

    def browse_directory(self):
        """List subdirectories for the folder browser."""
        home = os.path.expanduser("~")
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        req_path = qs.get("path", [None])[0]
        target = os.path.realpath(req_path) if req_path else home
        # Security: restrict to home directory
        if not target.startswith(home):
            self.send_json(403, {"error": "Access denied"})
            return
        if not os.path.isdir(target):
            self.send_json(404, {"error": "Directory not found"})
            return
        try:
            raw = os.listdir(target)
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
            return
        # Filter to visible directories only
        dirs = []
        for name in sorted(raw, key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if not os.path.isdir(full):
                continue
            # Count .md files and check for _order.yaml
            try:
                children = os.listdir(full)
            except PermissionError:
                children = []
            md_count = sum(1 for c in children if c.endswith(".md"))
            is_project = "_order.yaml" in children or md_count > 0
            dirs.append({"name": name, "isProject": is_project, "mdCount": md_count})
            if len(dirs) >= 200:
                break
        # Current directory info
        try:
            cur_children = os.listdir(target)
        except PermissionError:
            cur_children = []
        cur_md = sum(1 for c in cur_children if c.endswith(".md"))
        cur_is_project = "_order.yaml" in cur_children or cur_md > 0
        parent = os.path.dirname(target)
        if not parent.startswith(home):
            parent = None
        self.send_json(200, {
            "path": target,
            "home": home,
            "parent": parent,
            "atHome": target == home,
            "isProject": cur_is_project,
            "mdCount": cur_md,
            "entries": dirs,
        })

    def open_folder(self):
        """Switch to a local directory as the project."""
        global CHAPTERS_DIR, BACALHAU_FILE, TEMP_DIR
        home = os.path.expanduser("~")
        body = self.read_body()
        req_path = (body.get("path") or "").strip()
        if not req_path:
            self.send_json(400, {"error": "No path specified"})
            return
        target = os.path.realpath(req_path)
        if not target.startswith(home):
            self.send_json(403, {"error": "Access denied"})
            return
        if not os.path.isdir(target):
            self.send_json(404, {"error": "Directory not found"})
            return
        # Save current project if it's a .bacalhau
        _repack_bacalhau()
        old_temp = TEMP_DIR
        CHAPTERS_DIR = target
        BACALHAU_FILE = None
        TEMP_DIR = None
        if old_temp and os.path.isdir(old_temp):
            shutil.rmtree(old_temp, ignore_errors=True)
        self.send_json(200, {"ok": True, "path": target})

    # ── Themes ─────────────────────────────────────────────────────────────────

    def serve_themes_list(self):
        themes = list_themes()
        self.send_json(200, {"themes": themes})

    def serve_theme_css(self):
        name = self.path.split("/api/themes/", 1)[1]
        name = urllib.parse.unquote(name)
        # Prevent path traversal
        if "/" in name or name.startswith("."):
            self.send_json(400, {"error": "Invalid theme name"})
            return
        filepath = find_theme(name)
        if not filepath or not name.endswith(".css"):
            self.send_json(404, {"error": "Theme not found"})
            return
        with open(filepath, "r") as f:
            css = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.end_headers()
        self.wfile.write(css.encode())

    def import_theme(self):
        """Import a user-uploaded CSS theme file."""
        import base64
        body = self.read_body()
        if not body or "data" not in body or "filename" not in body:
            self.send_json(400, {"error": "Missing file data"})
            return
        name = body["filename"]
        if not name.endswith(".css") or "/" in name or name.startswith("."):
            self.send_json(400, {"error": "Invalid theme filename"})
            return
        try:
            raw = base64.b64decode(body["data"])
        except Exception:
            self.send_json(400, {"error": "Invalid file data"})
            return
        dest = os.path.join(_user_themes_dir(), name)
        with open(dest, "wb") as f:
            f.write(raw)
        self.send_json(200, {"ok": True, "name": name})

    # ── Git endpoints ──────────────────────────────────────────────────────────

    def git_status(self):
        installed = _git_installed()
        root = _git_root()
        is_temp = bool(TEMP_DIR and CHAPTERS_DIR and CHAPTERS_DIR.startswith(TEMP_DIR))
        if not installed:
            self.send_json(200, {"git_installed": False, "is_repo": False, "is_temp": is_temp, "files": []})
            return
        if not root:
            self.send_json(200, {"git_installed": True, "is_repo": False, "is_temp": is_temp, "files": []})
            return
        # Scope status to CHAPTERS_DIR (and its parent project dir) so we
        # don't show unrelated files when the project is inside a larger repo
        scope = CHAPTERS_DIR
        parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent  # include project-level files too (e.g. _order.yaml)
        rc, out, err = _run_git("status", "--porcelain=v1", "-uall", "--", scope)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Compute relative prefix so we can show short paths
        rel_prefix = ""
        if root and scope and scope.startswith(root):
            rel_prefix = os.path.relpath(scope, root)
            if rel_prefix == ".":
                rel_prefix = ""
        files = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path = line[3:]
            # Handle renamed files (old -> new)
            if " -> " in path:
                path = path.split(" -> ")[-1]
            # Strip the scope prefix for cleaner display
            if rel_prefix and path.startswith(rel_prefix + "/"):
                path = path[len(rel_prefix) + 1:]
            if index_status not in (" ", "?"):
                files.append({"path": path, "status": index_status, "staged": True})
            if worktree_status not in (" ", ""):
                st = "?" if worktree_status == "?" else worktree_status
                files.append({"path": path, "status": st, "staged": False})
        self.send_json(200, {"git_installed": True, "is_repo": True, "is_temp": is_temp, "files": files})

    def git_init(self):
        is_temp = bool(TEMP_DIR and CHAPTERS_DIR and CHAPTERS_DIR.startswith(TEMP_DIR))
        if is_temp:
            self.send_json(400, {"error": "Cannot initialize git in a temporary project"})
            return
        # Prefer parent of chapters/ as the repo root
        root = CHAPTERS_DIR
        if root and os.path.basename(root) == "chapters":
            root = os.path.dirname(root)
        rc, out, err = _run_git("init", cwd=root)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_stage(self):
        body = self.read_body()
        if body.get("all"):
            # Stage all within the project scope
            scope = CHAPTERS_DIR
            parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
            root = _git_root()
            if parent and parent != root:
                scope = parent
            rc, out, err = _run_git("add", "--", scope)
        else:
            path = body.get("path", "")
            if not path:
                self.send_json(400, {"error": "No path specified"})
                return
            rc, out, err = _run_git("add", "--", _git_resolve_path(path))
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_unstage(self):
        body = self.read_body()
        has_commits = _git_has_commits()
        if body.get("all"):
            # Unstage all within the project scope
            scope = CHAPTERS_DIR
            parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
            root = _git_root()
            if parent and parent != root:
                scope = parent
            if has_commits:
                rc, out, err = _run_git("reset", "HEAD", "--", scope)
            else:
                rc, out, err = _run_git("rm", "--cached", "-r", "--", scope)
        else:
            path = body.get("path", "")
            if not path:
                self.send_json(400, {"error": "No path specified"})
                return
            full_path = _git_resolve_path(path)
            if has_commits:
                rc, out, err = _run_git("reset", "HEAD", "--", full_path)
            else:
                rc, out, err = _run_git("rm", "--cached", "--", full_path)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_commit(self):
        body = self.read_body()
        msg = (body.get("message") or "").strip()
        if not msg:
            self.send_json(400, {"error": "Commit message required"})
            return
        rc, out, err = _run_git("commit", "-m", msg)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Extract short SHA from output like "[main abc1234] message"
        sha = ""
        m = re.search(r'\[[\w/.-]+ ([a-f0-9]+)\]', out)
        if m:
            sha = m.group(1)
        self.send_json(200, {"ok": True, "sha": sha})

    def git_log(self):
        if not _git_installed() or not _git_root():
            self.send_json(200, {"commits": []})
            return
        # Scope log to the project directory
        scope = CHAPTERS_DIR
        root = _git_root()
        parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent
        rc, out, err = _run_git(
            "log", "--format=%H\t%h\t%s\t%ar", "-20", "--", scope
        )
        if rc != 0:
            self.send_json(200, {"commits": []})
            return
        commits = []
        for line in out.strip().splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0],
                    "short": parts[1],
                    "message": parts[2],
                    "when": parts[3],
                })
        self.send_json(200, {"commits": commits})

    def git_restore(self):
        body = self.read_body()
        sha = (body.get("sha") or "").strip()
        if not sha:
            self.send_json(400, {"error": "No commit specified"})
            return
        if not _git_has_commits():
            self.send_json(400, {"error": "No commits to restore from"})
            return
        # Scope restore to the project directory
        scope = CHAPTERS_DIR
        root = _git_root()
        parent = os.path.dirname(CHAPTERS_DIR) if CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent
        # Checkout all project files from that commit
        rc, out, err = _run_git("checkout", sha, "--", scope)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Find the original commit message for the auto-commit
        rc2, msg_out, _ = _run_git("log", "--format=%s", "-1", sha)
        orig_msg = msg_out.strip() if rc2 == 0 else sha[:7]
        # Stage and auto-commit
        _run_git("add", "--", scope)
        rc3, out3, err3 = _run_git("commit", "-m", "Restored to: " + orig_msg)
        if rc3 != 0:
            # Might fail if nothing actually changed
            if "nothing to commit" in err3 or "nothing to commit" in out3:
                self.send_json(200, {"ok": True, "message": "Already at that version"})
                return
            self.send_json(500, {"error": err3.strip()})
            return
        self.send_json(200, {"ok": True, "message": "Restored to: " + orig_msg})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


# ── Bacalhau file helpers ─────────────────────────────────────────────────────

def _repack_bacalhau():
    """Re-pack the working directory back into the .bacalhau file."""
    if not BACALHAU_FILE:
        return
    import zipfile
    tmp_path = BACALHAU_FILE + ".tmp"
    project_root = os.path.dirname(CHAPTERS_DIR)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(CHAPTERS_DIR):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    filepath = os.path.join(root, fname)
                    arcname = os.path.relpath(filepath, CHAPTERS_DIR)
                    zf.write(filepath, os.path.join("chapters", arcname))
            latex_dir = os.path.join(project_root, "latex")
            if os.path.isdir(latex_dir):
                for root2, dirs2, files2 in os.walk(latex_dir):
                    dirs2[:] = [d for d in dirs2 if not d.startswith(".")]
                    for fname in sorted(files2):
                        if fname.startswith("."):
                            continue
                        filepath = os.path.join(root2, fname)
                        arcname = os.path.relpath(filepath, latex_dir)
                        zf.write(filepath, os.path.join("latex", arcname))
        os.replace(tmp_path, BACALHAU_FILE)
        print(f"Saved: {BACALHAU_FILE}", file=sys.stderr)
    except Exception as e:
        print(f"Error saving .bacalhau: {e}", file=sys.stderr)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── App window ────────────────────────────────────────────────────────────────

def _open_app_window(url):
    """Open the editor in a browser. Prefers the system default browser.
    Falls back to Chromium --app mode if webbrowser.open() fails."""
    try:
        webbrowser.open(url)
    except Exception:
        # Last resort: try Chromium --app mode
        candidates = []
        if sys.platform == "darwin":
            for app in ("Google Chrome", "Microsoft Edge", "Chromium"):
                candidates.append(f"/Applications/{app}.app/Contents/MacOS/{app}")
        else:
            for name in ("google-chrome", "google-chrome-stable", "chromium-browser",
                          "chromium", "microsoft-edge"):
                path = shutil.which(name)
                if path:
                    candidates.append(path)
        for browser in candidates:
            if os.path.isfile(browser):
                try:
                    subprocess.Popen([browser, f"--app={url}"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global CHAPTERS_DIR, BACALHAU_FILE, TEMP_DIR

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
        TEMP_DIR = tempfile.mkdtemp(prefix="bacalhau-empty-")
        CHAPTERS_DIR = os.path.join(TEMP_DIR, "chapters")
        os.makedirs(CHAPTERS_DIR)
        def _cleanup_temp():
            if TEMP_DIR and os.path.isdir(TEMP_DIR):
                shutil.rmtree(TEMP_DIR, ignore_errors=True)
        atexit.register(_cleanup_temp)
    else:
        # Handle .bacalhau file: extract to temp dir
        project_dir = os.path.abspath(project_dir)
        if project_dir.endswith(".bacalhau") and os.path.isfile(project_dir):
            import zipfile
            BACALHAU_FILE = project_dir
            TEMP_DIR = tempfile.mkdtemp(prefix="bacalhau-")
            with zipfile.ZipFile(BACALHAU_FILE, "r") as zf:
                # Zip-slip protection
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(TEMP_DIR, member))
                    if not target.startswith(os.path.realpath(TEMP_DIR) + os.sep) and target != os.path.realpath(TEMP_DIR):
                        print(f"Error: unsafe path in archive: {member}", file=sys.stderr)
                        sys.exit(1)
                zf.extractall(TEMP_DIR)
            chapters_path = os.path.join(TEMP_DIR, "chapters")
            if not os.path.isdir(chapters_path):
                print(f"Error: no chapters/ directory in {BACALHAU_FILE}", file=sys.stderr)
                shutil.rmtree(TEMP_DIR, ignore_errors=True)
                sys.exit(1)
            CHAPTERS_DIR = chapters_path
            def _cleanup_temp():
                if TEMP_DIR and os.path.isdir(TEMP_DIR):
                    shutil.rmtree(TEMP_DIR, ignore_errors=True)
            atexit.register(_cleanup_temp)
            print(f"Opened: {BACALHAU_FILE} → {TEMP_DIR}")
        else:
            CHAPTERS_DIR = project_dir
            if not os.path.isdir(CHAPTERS_DIR):
                print(f"Error: not a directory: {CHAPTERS_DIR}", file=sys.stderr)
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
    print(f"Bacalhau: {url} — editing {CHAPTERS_DIR}")
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
    global _last_heartbeat
    _last_heartbeat = time.time()
    def _heartbeat_watchdog():
        time.sleep(30)  # Grace period for browser to connect
        while True:
            time.sleep(15)
            if time.time() - _last_heartbeat > 120:
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
