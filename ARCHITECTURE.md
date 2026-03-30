# Bacalhau Architecture Specification

This document defines the interfaces between Bacalhau's modules. Any implementation conforming to these contracts — in any language or runtime — will produce a functional Bacalhau installation.

---

## System Overview

```
┌─────────────────────────────────────────────────────┐
│  Browser (frontend)                                  │
│  static/index.html + static/style.css + static/app.js│
│                                                       │
│  Communicates via HTTP JSON API on localhost          │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP (localhost only)
┌──────────────────────▼──────────────────────────────┐
│  Server (backend)                                    │
│  Serves static files, handles API routes             │
│                                                       │
│  Reads/writes project files on local filesystem      │
│  Shells out to git for version control               │
└──────────────────────┬──────────────────────────────┘
                       │ Filesystem + subprocess
┌──────────────────────▼──────────────────────────────┐
│  Project directory (chapters/, _order.yaml, .git/)   │
│  Themes directory (CSS files)                        │
│  Vendor directory (markdown-it.min.js, pdfme, etc.)  │
└─────────────────────────────────────────────────────┘
```

The frontend and backend communicate **exclusively** via the HTTP API defined below. There is no shared memory, no WebSocket, no server-side rendering. The frontend is a static single-page app; the backend is a stateless HTTP server with mutable filesystem access.

---

## 1. Shared State

The backend maintains five mutable globals that govern its behaviour:

| Name | Type | Description |
|------|------|-------------|
| `CHAPTERS_DIR` | string or null | Absolute path to the active project's markdown directory |
| `BACALHAU_FILE` | string or null | Absolute path to the `.bacalhau` file (if opened from one via CLI) |
| `BACALHAU_NAME` | string or null | Original filename of a browser-uploaded `.bacalhau` file |
| `TEMP_DIR` | string or null | Absolute path to temp extraction directory (cleaned up on exit) |
| `_last_heartbeat` | float | Unix timestamp of last heartbeat from the browser |

These are set by the entry point at startup and mutated by the `open/project` and `open/folder` API endpoints. All other backend code reads them but does not set them (except `_last_heartbeat`, updated by the heartbeat endpoint).

---

## 2. HTTP API Contract

All API endpoints are served on `http://127.0.0.1:<port>`. Requests and responses use `Content-Type: application/json` unless otherwise noted. Errors return `{"error": "<message>"}`.

### 2.1 Static Assets

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | `text/html` — the main SPA (`static/index.html`) |
| GET | `/favicon.png` | `image/png` — app icon |
| GET | `/static/<name>` | Static file from `static/` directory (html, css, js) |
| GET | `/vendor/<name>` | Vendored library from `vendor/` directory |
| GET | `/api/themes/<name>` | `text/css` — theme CSS file |

### 2.2 Project Tree

#### `GET /api/tree`
Returns the hierarchical file structure of `CHAPTERS_DIR`.

```json
{
  "tree": [
    {
      "type": "file",
      "name": "title.md",
      "path": "title.md",
      "heading": "The Salted Page",
      "writable": true
    },
    {
      "type": "dir",
      "name": "part-one",
      "path": "part-one/",
      "heading": "Part One",
      "children": [ ... ]
    }
  ],
  "project": "my-novel"
}
```

- `tree`: recursive array of nodes. Directories have `children`.
- `path`: relative to `CHAPTERS_DIR`. Directories end with `/`.
- `heading`: first `# Heading` found in the file (or `_part.md` for dirs). May be empty.
- `writable`: `false` if the file has its read-only flag set.
- `project`: display name derived from `BACALHAU_NAME`, `BACALHAU_FILE`, or directory name.
- Ordering follows `_order.yaml` in each directory. Unlisted files are appended alphabetically.

#### `GET /api/chapter/<path>`
Returns file content.
```json
{"content": "# Title\n\nBody text..."}
```

#### `PUT /api/chapter/<path>`
Save file content. Body: `{"content": "..."}`. Response: `{"ok": true}`.

#### `GET /api/preview`
Returns all markdown files in order for rendering.
```json
{
  "files": [
    {"path": "title.md", "content": "# Title\n\n..."},
    {"path": "part-one/scene-01.md", "content": "### Scene\n\n..."}
  ]
}
```

### 2.3 File Operations

All return `{"ok": true}` or `{"error": "..."}`.

| Method | Path | Body | Effect |
|--------|------|------|--------|
| POST | `/api/chapter/new` | `{slug, dir, position?, autoIncrement?}` | Create new `.md` file |
| POST | `/api/dir/new` | `{name, dir, position?, autoIncrement?}` | Create new directory |
| POST | `/api/rename` | `{path, newName, type}` | Rename file or directory |
| POST | `/api/tree/move` | `{src, src_type, dest_dir, position}` | Move/reorder item |
| POST | `/api/chapter/<path>/copy` | — | Duplicate file |
| POST | `/api/dir/<path>/copy` | — | Duplicate directory |
| POST | `/api/chapter/<path>/chmod` | — | Toggle read-only flag |
| DELETE | `/api/chapter/<path>` | — | Delete file |
| DELETE | `/api/dir/<path>` | — | Delete directory |

- `position`: integer index in the parent's `_order.yaml`. Omit to append.
- `autoIncrement`: if `true` and the slug conflicts, append `-2`, `-3`, etc.
- All operations update the relevant `_order.yaml` files.

### 2.4 Export

| Method | Path | Response |
|--------|------|----------|
| GET | `/api/export/markdown` | `application/octet-stream` — assembled `.md` with scene numbers |
| GET | `/api/export/pdf` | `application/pdf` — rendered PDF |
| GET | `/api/save/zip` | `application/octet-stream` — `chapters/` as `.zip` |
| GET | `/api/save/bacalhau` | JSON `{"message","path"}` if in-place save, or `application/octet-stream` download |

### 2.5 Project Opening

#### `POST /api/open`
Open an uploaded `.bacalhau` file. Body: `{"filename": "novel.bacalhau", "data": "<base64>"}`.
Extracts to a temp directory, sets `CHAPTERS_DIR`, `BACALHAU_NAME`, `TEMP_DIR`.
Response: `{"ok": true, "name": "novel.bacalhau"}`.

#### `POST /api/open/folder`
Switch to a local directory. Body: `{"path": "/Users/alice/novel/chapters"}`.
Restricted to the user's home directory. Sets `CHAPTERS_DIR`, clears `BACALHAU_FILE`/`TEMP_DIR`.
Response: `{"ok": true, "path": "/Users/alice/novel/chapters"}`.

### 2.6 Folder Browser

#### `GET /api/browse?path=<url-encoded-path>`
List subdirectories. If no `path`, defaults to home directory. Restricted to home.

```json
{
  "path": "/Users/alice/Documents",
  "home": "/Users/alice",
  "parent": "/Users/alice",
  "atHome": false,
  "isProject": true,
  "mdCount": 5,
  "entries": [
    {"name": "novel", "isProject": true, "mdCount": 12},
    {"name": "notes", "isProject": false, "mdCount": 0}
  ]
}
```

- `isProject`: `true` if directory contains `_order.yaml` or any `.md` files.
- `mdCount`: count of `.md` files in that directory (non-recursive).
- Hidden directories (starting with `.`) are excluded.

### 2.7 Git Integration

All git operations shell out to the system `git` binary. The git root is the closest ancestor of `CHAPTERS_DIR` containing `.git/`.

#### `GET /api/git/status`
```json
{
  "git_installed": true,
  "is_repo": true,
  "is_temp": false,
  "files": [
    {"path": "scene-01.md", "status": "M", "staged": true},
    {"path": "new-file.md", "status": "?", "staged": false}
  ]
}
```
- `status`: one of `M` (modified), `A` (added), `D` (deleted), `?` (untracked), `R` (renamed).
- `staged`: `true` if in the index, `false` if in the working tree.
- Paths are relative to the project scope, not the git root.
- Auto-stages all unstaged changes on every refresh (frontend behaviour).

#### `POST /api/git/init`
Initialize a new git repo. Body: `{}`. Response: `{"ok": true}`.

#### `POST /api/git/stage`
Body: `{"path": "scene-01.md"}` or `{"all": true}`. Response: `{"ok": true}`.

#### `POST /api/git/unstage`
Body: `{"path": "scene-01.md"}` or `{"all": true}`. Response: `{"ok": true}`.
Uses `git rm --cached` if no commits exist yet.

#### `POST /api/git/commit`
Body: `{"message": "finished chapter 3"}`. Auto-stages all changes before committing.
Response: `{"ok": true, "sha": "abc1234"}`.

#### `GET /api/git/log`
Returns last 20 commits touching the project scope.
```json
{
  "commits": [
    {"sha": "abc1234...", "short": "abc1234", "message": "finished chapter 3", "when": "2 hours ago"}
  ]
}
```

#### `POST /api/git/restore`
Body: `{"sha": "abc1234..."}`. Auto-saves current state before restoring.
Checks out project files from the specified commit and creates a new commit: `"Restored to: <original message>"`.
Response: `{"ok": true, "message": "Restored to: finished chapter 3"}`.

### 2.8 Themes

#### `GET /api/themes`
```json
{"themes": ["azulejo.css", "azulejo-dark.css", "calcada.css", "calcada-dark.css"]}
```

#### `POST /api/themes/import`
Body: `{"filename": "my-theme.css", "data": "<base64>"}`. Saves to user themes directory.
Response: `{"ok": true, "name": "my-theme.css"}`.

### 2.9 Lifecycle

#### `GET /api/heartbeat`
Response: `{"ok": true}`. Updates `_last_heartbeat`. Frontend sends this every 10 seconds.

#### `POST /api/shutdown`
Repacks `.bacalhau` if applicable, then exits. Sent via `navigator.sendBeacon` on page close.

---

## 3. File Format Contracts

### 3.1 Project Directory

A Bacalhau project is a directory containing `.md` files, optionally organized in subdirectories. Each directory may contain an `_order.yaml` to control sibling order.

```
chapters/
  _order.yaml          # optional: controls ordering
  title.md
  part-one/
    _order.yaml
    _part.md            # optional: rendered as section heading
    scene-01.md
    scene-02.md
```

### 3.2 `_order.yaml`

Plain text, one entry per line, prefixed with `- `. Directories end with `/`.

```yaml
- title.md
- part-one/
- part-two/
```

Unlisted files/dirs are appended alphabetically. Missing file = ignored silently.

### 3.3 `.bacalhau` Format

A ZIP file with `.bacalhau` extension containing:

```
chapters/           # required — the project files
  _order.yaml
  ...
latex/              # optional — LaTeX assets
  ...
.git/               # optional — version history (bundled for portability)
  ...
```

When opened, extracted to a temp directory. On save, repacked with all three directories.

### 3.4 Theme CSS

A CSS file that overrides `:root` custom properties:

```css
:root {
  --bg: #ece6dc;      /* main background */
  --bg2: #e2dbd0;     /* sidebar/header background */
  --bg3: #d6cfc3;     /* hover/active background */
  --bg4: #cbc3b6;     /* secondary active */
  --fg: #2b2b2b;      /* main text */
  --fg2: #4a4a48;     /* secondary text */
  --fg3: #7a7a76;     /* tertiary/dim text */
  --accent: #4a4a48;  /* accent colour (links, active states) */
  --border: #c0b9ad;  /* borders and dividers */
}
```

Optional overrides for fonts and element-specific styling.

---

## 4. Module Responsibilities

### Entry Point (`editor.py`)
- Parse CLI arguments (project path, port)
- Initialize shared state (set `CHAPTERS_DIR`, etc.)
- Extract `.bacalhau` to temp dir if applicable
- Start HTTP server
- Launch browser window
- Run heartbeat watchdog thread
- Handle signals (SIGTERM, SIGHUP) for graceful shutdown
- Repack `.bacalhau` on exit

### Server (`server.py`)
- HTTP request handler implementing all routes in Section 2
- Serves static files from `static/` and `vendor/`
- Calls helper functions for filesystem/git operations
- Stateless except for reading/writing shared state

### Helpers (`helpers.py`)
- Pure filesystem operations: read/write `_order.yaml`, build tree, walk files
- Git operations: shell out to `git`, parse porcelain output
- Theme management: list/find/import themes
- Bacalhau repack: ZIP assembly with chapters, latex, .git
- No HTTP awareness — these are called by the server, never call it

### Frontend (`static/`)
- Single-page app: HTML shell, CSS, JavaScript
- Communicates with backend exclusively via `fetch()` to the API
- Renders markdown via vendored `markdown-it`
- Manages all UI state (active file, scroll sync, git panel, etc.)
- Sends heartbeat every 10 seconds
- Sends shutdown beacon on page close

### Shared State (`state.py`)
- Five mutable variables (see Section 1)
- No logic — only data
- Imported by server and helpers

---

## 5. Security Boundaries

- Server binds to `127.0.0.1` only — not accessible from the network.
- `resolve_path()` prevents path traversal (rejects paths escaping `CHAPTERS_DIR`).
- Folder browser restricted to the user's home directory.
- ZIP extraction includes zip-slip protection.
- Vendor file serving rejects paths containing `/` or starting with `.`.
- Theme import validates filename (no path separators or dotfiles).
- No user authentication — single-user, localhost only.
