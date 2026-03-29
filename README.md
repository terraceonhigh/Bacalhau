# Bacalhau

A browser-based manuscript editor. Zero runtime dependencies. One Python file.

---

## What this is

A three-pane editor for long-form writing projects organized as hierarchical markdown files:

- **Left:** File tree sidebar with drag-and-drop reordering, inline rename, and collapse/expand
- **Center:** Continuous scroll markdown editor with per-file auto-save and spell check
- **Right:** Live preview of the full manuscript with auto-numbering and scroll sync

## Getting started

### Native app (recommended)

1. Download from [Releases](https://github.com/terraceonhigh/Bacalhau/releases):
   - **macOS:** `Bacalhau-...-macos.zip` (contains `Bacalhau.app`)
   - **Linux:** `Bacalhau-...-linux.AppImage` (single file)
2. Place in your project folder (next to your `chapters/` directory, or empty — it creates one)
3. Double-click. A browser tab opens. Start writing.

On macOS, first launch requires right-click → Open (unsigned app). On Linux, `chmod +x` the AppImage first.

### Portable (no build)

1. Download the portable zip from Releases
2. Unzip into your project folder
3. Launch:
   - **macOS:** Double-click `Bacalhau.command`
   - **Linux:** Run `./Bacalhau` in a terminal

### From the command line (any Unix)

```bash
./Bacalhau                              # open the current directory
./Bacalhau /path/to/project             # open a specific directory
python3 editor.py <project-directory>   # direct invocation
python3 editor.py --port 8080           # custom port
python3 editor.py                       # defaults to chapters/ next to the script
```

## Runtime requirements

| Requirement | Version | Notes |
|------------|---------|-------|
| Python 3 | 3.6+ | The only runtime dependency. Pre-installed on macOS and most Linux. |
| A web browser | Any modern | Chrome, Firefox, Safari, Edge. The editor serves on `localhost:3000`. |

**No other dependencies.** No npm, no pip packages, no CDN loads, no node_modules. The editor is Python stdlib + inline HTML/CSS/JS.

### Optional (for PDF export via `assemble.py`)

| Tool | Install | Purpose |
|------|---------|---------|
| Pandoc | `brew install pandoc` | Markdown → LaTeX/PDF conversion |
| XeLaTeX | `brew install --cask mactex-no-gui` | PDF rendering with custom fonts |

## Project structure

A Bacalhau project is just a directory of markdown files:

```
my-novel/
  Bacalhau.app              # the editor (place here, double-click)
  chapters/                 # created automatically on first launch
    _order.yaml             # controls top-level order
    title.md
    part-one/
      _order.yaml           # controls order within this part
      _part.md              # part heading (## Part One)
      chapter-one.md
      chapter-two.md
    part-two/
      _order.yaml
      _part.md
      chapter-three.md
  themes/                   # optional — CSS theme files
    azulejo.css
    cobblestone-dark.css
```

### `_order.yaml`

A simple list of filenames/directories controlling sibling order:

```yaml
- title.md
- part-one/
- part-two/
```

Directories end with `/`. Files or directories not listed are appended alphabetically. If `_order.yaml` is missing, everything is sorted alphabetically.

### `_part.md`

Optional file inside a directory containing a section heading (e.g., `## Part One`). Rendered before the directory's other files in the preview.

### `themes/`

Optional directory next to `chapters/` containing `.css` files. Each file appears in the theme dropdown in the sidebar. Themes override CSS custom properties (`--bg`, `--accent`, etc.) to restyle the entire editor. See `DESIGN.md` for the full list of variables.

## Assembly & export

### From the editor

- **Save .zip** — downloads `chapters/` as a zip archive
- **Export .md** — assembles all chapters into a single numbered markdown file

### From the command line

```bash
python3 assemble.py chapters/ --concat -o manuscript.md
python3 assemble.py chapters/ --latex -o manuscript.tex --templates latex/
python3 assemble.py chapters/ --pdf -o manuscript.pdf --templates latex/
```

Scene headings (`### Title`) are auto-numbered sequentially, skipping `_part.md` files and files prefixed with `intermezzo-`.

## Features

- **Zero dependencies.** Python 3 stdlib only.
- **Single file.** The editor is one Python file (~60KB) with inline HTML/CSS/JS.
- **Filesystem is truth.** The directory structure IS the hierarchy. `_order.yaml` files control ordering.
- **Continuous scroll editor.** All files as stacked textareas with file header bars between them.
- **Auto-save.** Per-file, 1 second after you stop typing. `Cmd+S` to force-save all.
- **Drag-and-drop.** Reorder files and directories. Drag a directory to move the entire subtree.
- **Inline rename.** Double-click a label or use the `rn` button. Updates the `###` heading to match.
- **File operations.** Copy, delete, read-only toggle (via filesystem permissions) — on hover meatball menu.
- **New file/folder.** Click between items (PowerPoint-style) or use sidebar buttons. Creates inline, then renames.
- **Auto-numbering.** Scene headings numbered by position, not stored in the files. Reorder and numbers update.
- **Themes.** CSS files in `themes/` directory. Dropdown in sidebar. Persists in `localStorage`.
- **Scroll sync.** Proportional per-file sync at display refresh rate. Centers on the viewport midpoint.
- **Arbitrary depth.** Nest directories as deep as you like.
- **PID management.** Prints PID on startup. SIGTERM/SIGHUP handled for clean shutdown. Relaunching kills the old instance.
- **Favicon.** Serves `icon.png` as the browser tab icon.
- **Themed scrollbars.** WebKit + Firefox scrollbar styling follows the active theme.

## Files

| File | What it is |
|------|-----------|
| `editor.py` | The editor — serves the browser UI and all file APIs |
| `assemble.py` | Concatenates a project into a single markdown/LaTeX/PDF |
| `Bacalhau.command` | macOS portable launcher (double-click in Finder) |
| `Bacalhau` | Unix portable launcher (shell script, works everywhere) |
| `Bacalhau.desktop` | Linux portable launcher (freedesktop `.desktop` entry) |
| `DESIGN.md` | UI patterns, color system, interaction conventions |
| `CREDITS.md` | Icon attribution |
| `icon.png` | Source icon — "Azulejos Portugueses - 11" by r2hox, CC BY-SA 2.0 |
| `icon-source.jpg` | Original JPEG from Flickr |
| `build.sh` | Produces `.app` (macOS) and `.AppDir`/`.AppImage` (Linux) |
| `release.sh` | Runs `build.sh` then packages all release artifacts |
| `make_icon.py` | Generates a placeholder icon (development only) |
| `packaging/` | Platform-specific launcher scripts and `Info.plist` template |

## Build requirements (development only)

These are only needed if you're building the native packages — not for using the editor.

| Tool | Platform | Purpose |
|------|----------|---------|
| `sips` | macOS (built-in) | Resizes icon PNG to required sizes |
| `iconutil` | macOS (built-in) | Converts `.iconset` to `.icns` |
| `appimagetool` | Linux | Packages AppDir into `.AppImage` |
| `zip` | Any Unix | Packaging release zips |

```bash
./build.sh v1.0          # builds .app + .AppDir
./release.sh v1.0        # builds + packages all release artifacts
```

## Design

See `DESIGN.md` for the full UI specification: layout, color system, drag-and-drop conventions, the mouseup pattern for draggable elements, scroll sync architecture, and theming.

## Known limitations

- **No collaborative editing.** Single-user, local files only.
- **No syntax highlighting** in the editor. It's a `<textarea>`, not CodeMirror. This is intentional — zero dependencies.
- **Scroll sync is proportional, not line-exact.** The editor and preview render at different heights per file. The midpoint is synced; top and bottom may drift.
- **Unsigned on macOS.** First launch requires right-click → Open. Signing requires an Apple Developer account ($99/year).
- **AppImage requires system Python.** The AppImage does not bundle a Python interpreter (Option A packaging). A future version may bundle one via PyInstaller (Option B).
