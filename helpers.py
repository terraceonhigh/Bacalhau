"""
Bacalhau — Helper functions extracted from editor.py.

Git helpers, filesystem helpers, .bacalhau file packing, and app window launcher.
All shared globals are accessed via the `state` module.
"""

import os
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser

import state


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_root():
    """Find the git root directory, or None."""
    if state.CHAPTERS_DIR and os.path.isdir(os.path.join(state.CHAPTERS_DIR, ".git")):
        return state.CHAPTERS_DIR
    parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
    if parent and os.path.isdir(os.path.join(parent, ".git")):
        return parent
    return None


def _run_git(*args, cwd=None):
    """Run a git command and return (returncode, stdout, stderr)."""
    git_cwd = cwd or _git_root() or state.CHAPTERS_DIR
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
    if not root or not state.CHAPTERS_DIR:
        return short_path
    scope = state.CHAPTERS_DIR
    parent = os.path.dirname(state.CHAPTERS_DIR)
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
    abspath = os.path.normpath(os.path.join(state.CHAPTERS_DIR, clean))
    if not abspath.startswith(state.CHAPTERS_DIR):
        raise ValueError("Path escape attempt")
    return abspath


# ── Bacalhau file helpers ─────────────────────────────────────────────────────

def _repack_bacalhau():
    """Re-pack the working directory back into the .bacalhau file."""
    if not state.BACALHAU_FILE:
        return
    import zipfile
    tmp_path = state.BACALHAU_FILE + ".tmp"
    project_root = os.path.dirname(state.CHAPTERS_DIR)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(state.CHAPTERS_DIR):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    filepath = os.path.join(root, fname)
                    arcname = os.path.relpath(filepath, state.CHAPTERS_DIR)
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
            # Bundle .git so version history travels with the file
            git_dir = os.path.join(project_root, ".git")
            if os.path.isdir(git_dir):
                for root3, dirs3, files3 in os.walk(git_dir):
                    for fname in files3:
                        filepath = os.path.join(root3, fname)
                        arcname = os.path.relpath(filepath, project_root)
                        zf.write(filepath, arcname)
        os.replace(tmp_path, state.BACALHAU_FILE)
        print(f"Saved: {state.BACALHAU_FILE}", file=sys.stderr)
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
