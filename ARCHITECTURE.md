# Bacalhau Architecture Specification

This document defines the interfaces between Bacalhau's modules. Any implementation conforming to these contracts will produce a functional Bacalhau installation.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Wails webview (WebKit on macOS/Linux, WebView2 on Windows) │
│  Loads / from the in-process AssetServer                    │
│  static/index.html + static/style.css + static/app.js       │
│                                                              │
│  Communicates with the backend in two ways:                  │
│    1. fetch() against the HTTP handler (in-process)          │
│    2. window.go.main.app.* — Wails-bound native methods      │
│       (OpenFile, SaveToFile) for OS file dialogs             │
└─────────────────────────────┬───────────────────────────────┘
                              │ in-process Go calls
┌─────────────────────────────▼───────────────────────────────┐
│  Go server (internal/server)                                 │
│  http.Handler wired into Wails AssetServer — there is no     │
│  TCP port; Wails dispatches requests directly.               │
│                                                              │
│  Reads/writes project files on the local filesystem.         │
│  Shells out to git for version control.                      │
└─────────────────────────────┬───────────────────────────────┘
                              │ filesystem + subprocess
┌─────────────────────────────▼───────────────────────────────┐
│  Project directory (chapters/, _order.yaml, optional .git/)  │
│  User themes directory (CSS files)                           │
│  Embedded assets (static/, vendor_js/, themes/) baked into   │
│  the Go binary via go:embed                                  │
└─────────────────────────────────────────────────────────────┘
```

The frontend talks to the backend over (a) the HTTP API defined below and (b) a small set of Wails-bound methods that need OS-level access (native file dialogs). There is no shared memory, no WebSocket, no server-side rendering. The frontend is a static single-page app; the backend is an in-process HTTP handler with mutable filesystem access.

---

## 1. Shared State

The backend maintains four mutable fields plus a heartbeat timestamp on a single `state.AppState` value (`internal/state/state.go`). All access is mediated by a `sync.RWMutex`.

| Field | Type | Description |
|------|------|-------------|
| `chaptersDir` | string | Absolute path to the active project's markdown directory |
| `bacalhauFile` | string | Absolute path to the `.bacalhau` file (when opened from one via CLI) |
| `bacalhauName` | string | Original filename when a `.bacalhau` was uploaded through the UI |
| `tempDir` | string | Absolute path to temp extraction directory (cleaned up at shutdown) |
| `lastHeartbeat` | time.Time | Timestamp of last `/api/heartbeat` from the frontend |

These are populated at startup by `main.go` and mutated by the `/api/open` and `/api/open/folder` endpoints. All other code reads them via the typed accessors (`ChaptersDir()`, `SetChaptersDir()`, etc.).

---

## 2. HTTP API Contract

The handler is served in-process via Wails `AssetServer.Handler`. The frontend issues normal `fetch()` calls against relative paths; there is no TCP port to connect to. Requests and responses use `Content-Type: application/json` unless otherwise noted. Errors return `{"error": "<message>"}`.

### 2.1 Static Assets

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | `text/html` — the main SPA (`static/index.html`) |
| GET | `/favicon.png` | `image/png` — app icon |
| GET | `/static/<path>` | Static file from embedded `static/` (html, css, js) |
| GET | `/vendor/<path>` | Vendored JS from embedded `vendor_js/` (currently `markdown-it.min.js`) |
| GET | `/api/themes/<name>` | `text/css` — theme CSS file (bundled or user-imported) |

### 2.2 Project Tree

#### `GET /api/tree`
Returns the hierarchical file structure of `chaptersDir`.

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
- `path`: relative to `chaptersDir`. Directories end with `/`.
- `heading`: first `# Heading` found in the file (or `_part.md` for dirs). May be empty.
- `writable`: `false` if the file has its read-only flag set.
- `project`: display name derived from `bacalhauName`, `bacalhauFile`, or the directory name.
- Ordering follows `_order.yaml` in each directory. Unlisted files are appended alphabetically.

#### `GET /api/chapter/<path>`
Returns file content:
```json
{"content": "# Title\n\nBody text..."}
```

#### `PUT /api/chapter/<path>`
Save file content. Body: `{"content": "..."}`. Response: `{"ok": true}`.

#### `GET /api/preview`
Returns all markdown files in order for rendering:
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
| POST | `/api/tree/move` | `{src, src_type, dest_dir, position}` | Move / reorder item |
| POST | `/api/chapter/<path>/copy` | — | Duplicate file |
| POST | `/api/dir/<path>/copy` | — | Duplicate directory |
| POST | `/api/chapter/<path>/chmod` | — | Toggle read-only flag |
| DELETE | `/api/chapter/<path>` | — | Delete file |
| DELETE | `/api/dir/<path>` | — | Delete directory |

- `position`: integer index in the parent's `_order.yaml`. Omit to append.
- `autoIncrement`: if `true` and the slug conflicts, append `-2`, `-3`, etc.
- All operations update the relevant `_order.yaml` files.

The router uses two catch-all dispatchers (`postChapterDispatch`, `postDirDispatch`) because Go 1.22 `http.ServeMux` doesn't support suffix matching; they route by trailing `/copy` or `/chmod`.

### 2.4 Export & Save

| Method | Path | Response |
|--------|------|----------|
| GET | `/api/export/markdown` | `text/markdown` — assembled `.md` with scene numbers |
| GET | `/api/export/html` | `text/html` — assembled HTML page that auto-triggers `window.print()` once loaded in a browser context |
| GET | `/api/save/zip` | `application/octet-stream` — `chapters/` as `.zip` |
| GET | `/api/save/bacalhau` | JSON `{"message","path"}` if in-place save, or `application/octet-stream` download |

### 2.5 Project Opening

#### `POST /api/open`
Open an uploaded `.bacalhau` file. Body: `{"filename": "novel.bacalhau", "data": "<base64>"}`.
Extracts to a temp directory, sets `chaptersDir`, `bacalhauName`, `tempDir`. Response: `{"ok": true, "name": "novel.bacalhau"}`.

In the desktop app, the frontend gets the base64 payload from the Wails-bound `app.OpenFile()` method (see Section 6).

#### `POST /api/open/folder`
Switch to a local directory. Body: `{"path": "/Users/alice/novel/chapters"}`.
Restricted to the user's home directory. Sets `chaptersDir`, clears `bacalhauFile` / `bacalhauName` / `tempDir`. Response: `{"ok": true, "path": "/Users/alice/novel/chapters"}`.

### 2.6 Folder Browser

#### `GET /api/browse?path=<url-encoded-path>`
List subdirectories. With no `path`, defaults to the home directory. Restricted to home.

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

- `isProject`: `true` if the directory contains `_order.yaml` or any `.md` files.
- `mdCount`: count of `.md` files in that directory (non-recursive).
- Hidden directories (starting with `.`) are excluded.

### 2.7 Git Integration

All git operations shell out to the system `git` binary. The git root is the closest ancestor of `chaptersDir` containing `.git/`.

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
- The frontend auto-stages all unstaged changes on every panel refresh.

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
Returns the last 20 commits touching the project scope:
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
Body: `{"filename": "my-theme.css", "data": "<base64>"}`. Saves to the platform-specific user themes directory.
Response: `{"ok": true, "name": "my-theme.css"}`.

### 2.9 Lifecycle

#### `GET /api/version`
Response: `{"version": "vX.Y.Z"}`. Set at build time via `-ldflags "-X main.version=..."`.

#### `GET /api/heartbeat`
Response: `{"ok": true}`. Updates `lastHeartbeat`. The frontend pings this periodically.

#### `POST /api/shutdown`
Calls `repackFn` (writes the current project back to its `.bacalhau` file if applicable), responds, and then asynchronously triggers `shutdownFn`, which calls Wails `Quit`. Used during clean app exit.

---

## 3. File Format Contracts

### 3.1 Project Directory

A Bacalhau project is a directory containing `.md` files, optionally organised in subdirectories. Each directory may contain an `_order.yaml` to control sibling order.

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

Unlisted files/dirs are appended alphabetically. A missing entry is ignored silently.

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

When opened, it is extracted to a temp directory. On save, it is repacked with all three directories.

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

Optional overrides for fonts and element-specific styling are supported.

---

## 4. Module Responsibilities

### `main.go` — Entry point
- Parses CLI args (project path).
- Initialises shared state (sets `chaptersDir`, `bacalhauFile`, `tempDir`).
- Extracts `.bacalhau` to a temp dir if applicable.
- Constructs the `*server.Server` and hands its `Handler()` to Wails as the AssetServer.
- Launches the Wails window (title, size, lifecycle hooks).
- Exposes Wails-bound methods (`OpenFile`, `SaveToFile`) — see Section 6.
- On Wails `OnShutdown`: repacks `.bacalhau` if applicable, then removes temp dirs.

### `internal/server/` — HTTP handler
- `server.go`: route table (`Handler()`) and two suffix dispatchers.
- `api_*.go`: one file per logical group of endpoints (`tree`, `chapter`, `files`, `export`, `project`, `git`, `themes`, `lifecycle`).
- `helpers.go`: shared utilities (`sendJSON`, path resolution, project-name derivation, etc.).
- Stateless except for reading/writing `*state.AppState` and a `fsMu` mutex around export operations.

### `internal/fs/` — Filesystem operations
- `tree.go`: `WalkFiles`, tree construction in `_order.yaml` order.
- `order.go`: parse / write `_order.yaml`.
- `bacalhau.go`: ZIP extract (with zip-slip protection) and repack for `.bacalhau` files.

### `internal/git/` — Version control
- Thin shell wrapper around the system `git` binary.
- Status parsing of `git status --porcelain`, scoped to the project subdirectory of the git root.

### `internal/state/` — Shared state
- `AppState` struct with mutex-protected getters and setters for the fields in Section 1.
- No business logic.

### `internal/themes/` — Theme management
- Discovery: list bundled themes (from the embedded `themes/` FS) and any user-imported themes from the platform-specific user data dir.
- Import: validate filename (no path separators or dotfiles) and write to the user themes directory.

### `static/` — Frontend SPA
- Single-page app: `index.html`, `style.css`, `app.js`.
- Communicates with the backend via `fetch()` to `/api/*` and (for OS file dialogs) via `window.go.main.app.*`.
- Renders markdown client-side using vendored `markdown-it`.
- Manages all UI state (active file, scroll sync, git panel, etc.).
- Sends heartbeat pings to `/api/heartbeat`.

### `vendor_js/` — Vendored JavaScript
- `markdown-it.min.js`. (The directory is named `vendor_js/` rather than `vendor/` to avoid conflicting with the Go module vendor convention.)

### `themes/` — Bundled CSS themes
- `azulejo.css`, `azulejo-dark.css`, `calcada.css`, `calcada-dark.css`. All four are embedded into the binary.

---

## 5. Wails-bound Methods

Two Go methods on `*app` (in `main.go`) are bound to the JavaScript runtime and accessible as `window.go.main.app.<Name>(...)`:

### `OpenFile() → {filename, data}`
Opens a native file-open dialog filtered to `*.bacalhau`. Returns the basename and base64-encoded contents of the chosen file, or `null` if cancelled. The frontend then POSTs the payload to `/api/open`.

### `SaveToFile(suggestedName, filterDesc, filterPattern, b64data) → path`
Opens a native save dialog with the given filename suggestion and file filter, then writes the decoded data to the chosen path. Returns the chosen path, or `""` if cancelled.

These exist because browsers (even inside a webview) cannot read or write arbitrary local paths without OS-level permission prompts. Wails provides the bridge.

---

## 6. Security Boundaries

- No TCP port is opened. The HTTP handler runs in-process behind Wails AssetServer and is not reachable from the network.
- `resolve_path()` prevents path traversal (rejects paths escaping `chaptersDir`).
- The folder browser is restricted to the user's home directory.
- ZIP extraction includes zip-slip protection.
- Vendor file serving rejects paths containing `..` or starting with `.`.
- Theme import validates the filename (no path separators or dotfiles).
- No user authentication — single-user desktop application.
- macOS releases are signed with a Developer ID certificate and Apple-notarized; the `.app` and `.dmg` are both stapled. The hardened runtime is enabled and entitlements are minimal (see `packaging/macos/Bacalhau.entitlements`).
