<p align="center">
  <img src="icons/icon.png" alt="Bacalhau" width="128">
</p>

# Bacalhau

A native desktop manuscript editor for long-form writing projects. Single Go binary, no dependencies.

---

![Bacalhau editor](icons/screenshot.png)

## What it does

Three-pane layout for editing hierarchical markdown:

- **Sidebar:** File tree with drag-and-drop reordering, inline rename, collapse/expand
- **Editor:** Continuous scroll with per-file auto-save, live preview, and seamless arrow-key navigation across files
- **Preview:** Full manuscript with auto-numbered scene headings and scroll sync
- **Git panel:** Stage, unstage, commit, and restore from history — no terminal required
- **Folder browser:** Open any directory from the UI without touching a terminal

Projects are stored as plain markdown files on disk, organized in directories with `_order.yaml` for ordering.

## Getting started

### Native app

Download from [Releases](https://github.com/terraceonhigh/Bacalhau/releases):

- **macOS:** `Bacalhau-...-macos.zip` (contains `Bacalhau.app`)

Double-click to launch. On first launch, right-click → Open (unsigned).

### Command line

```bash
./Bacalhau <project-directory>
./Bacalhau project.bacalhau        # open a .bacalhau file
./Bacalhau                         # no args — shows welcome screen
```

## Web Edition

A fully browser-based version runs at **https://terraceonhigh.github.io/Bacalhau/** — no install required.

- Open and save `.bacalhau` files via the browser's file picker
- Full editor: sidebar, drag-and-drop reordering, live preview, themes
- Real Git history via [isomorphic-git](https://isomorphic-git.org/) — commits made in the browser are visible in the desktop app and vice versa
- `_order.yaml` ordering interop — file order round-trips between web and desktop
- Session auto-saved to localStorage; survives page reloads
- Ctrl/Cmd+S saves, Ctrl/Cmd+O opens

**Not available in the web edition:** native filesystem access (save triggers a download), PDF export, folder browsing.

The web edition source lives in `docs/` on the `gh-pages-app` branch.

## Requirements

- **macOS 10.13+** or **Linux** with a display server
- **Go 1.22+** (build only — the compiled binary has no runtime dependencies)

## Project structure

```
my-novel/
  chapters/
    _order.yaml
    title.md
    part-one/
      _order.yaml
      _part.md            # section heading (## Part One)
      chapter-one.md
      chapter-two.md
```

### `_order.yaml`

Controls sibling order:

```yaml
- title.md
- part-one/
- part-two/
```

Directories end with `/`. Unlisted items are appended alphabetically. If the file is missing, everything is alphabetical.

### `_part.md`

Optional heading file inside a directory. Rendered before the directory's other files in the preview.

## Save and export

### From the editor (Save As menu)

- **Save .bacalhau** — portable project file (ZIP with custom extension, bundles `chapters/` and `.git/`)
- **Save .zip** — raw `chapters/` directory as a zip
- **Save .md** — assembled manuscript with scene numbers
- **Save .pdf** — rendered PDF

All save/export operations use native file dialogs.

### Opening .bacalhau files

```bash
./Bacalhau project.bacalhau
```

Or use Cmd+O / the Open button in the sidebar. The file is extracted to a temp directory; edits are saved back on close.

### Browsing folders

Click **Open Folder** in the sidebar footer to open a visual directory navigator. Browse your home directory, click into folders, and select one to open. Folders containing markdown files are highlighted with a count badge.

## Themes

Four themes are bundled: Azulejo, Azulejo Dark, Calçada, Calçada Dark. Select from the dropdown in the sidebar.

To add a custom theme, choose "Import theme..." from the dropdown and select a `.css` file. Imported themes are stored in:

- macOS: `~/Library/Application Support/Bacalhau/themes/`
- Linux: `~/.local/share/Bacalhau/themes/`

Themes override CSS custom properties (`--bg`, `--accent`, etc.). See `DESIGN.md` for the full variable list.

## Building

```bash
# Development (quick, no .app bundle)
CGO_LDFLAGS="-framework UniformTypeIdentifiers" go build -tags "desktop,production" -o Bacalhau .

# macOS .app with icon
./build-app.sh v3.0
```

| Tool | Platform | Purpose |
|------|----------|---------|
| Go 1.22+ | All | Compilation |
| `sips`, `iconutil` | macOS (built-in) | Icon conversion for .app bundle |

## Files

| Path | Description |
|------|-------------|
| `main.go` | Entry point — Wails app lifecycle, native file dialogs |
| `internal/server/` | HTTP handler — all API routes |
| `internal/fs/` | Filesystem ops — .bacalhau ZIP, `_order.yaml`, tree building |
| `internal/git/` | Git operations — shells out to `git` |
| `internal/state/` | Shared mutable state |
| `internal/themes/` | Theme CSS management |
| `static/` | Frontend SPA — HTML, CSS, JavaScript |
| `vendor_js/` | Vendored JS (markdown-it) |
| `themes/` | Bundled CSS themes |
| `demo/` | Sample project — *The Salted Page* |
| `icons/` | Icon assets and generator script |
| `packaging/` | macOS Info.plist template |
| `build-app.sh` | Builds `Bacalhau.app` for macOS |
| `docs/` | Web edition (GitHub Pages) — browser-only SPA |
| `DESIGN.md` | UI specification |
| `ARCHITECTURE.md` | API and module contracts |
| `CREDITS.md` | Icon attribution |

## Version control

The Git tab in the sidebar provides built-in version control:

- **Status:** See which files have changed (M/A/D/? badges)
- **Stage/Unstage:** Per-file or all-at-once, with `+` and `−` buttons
- **Commit:** Enter a message and checkpoint your work — all changes are staged automatically
- **History:** A stack of past commits with one-click **Restore** to roll back to any version

Restore is non-destructive — it auto-saves your current state before reverting, so every version is always recoverable. No git knowledge required.

If no repository exists, the panel offers an "Initialize Repository" button. Projects opened from `.bacalhau` files (temporary extraction) don't show git controls.

Requires `git` to be installed on the system. The panel degrades gracefully if it's not available.

## Demo project

A sample novella, *The Salted Page*, is included in `demo/chapters/` with two parts (Past and Present) to demonstrate the editor's hierarchical structure:

```bash
./Bacalhau demo/chapters
```

## Known limitations

- Single-user, local files only. No collaboration.
- The editor uses `<textarea>` — no syntax highlighting.
- Scroll sync is proportional, not line-exact. Drift increases toward the edges of long files.
- Unsigned on macOS. First launch requires right-click → Open.
- The folder browser is restricted to your home directory for security.
