# Editor Design Conventions

UI patterns and interaction design for the Bacalhau editor (`static/index.html` + `static/style.css` + `static/app.js`, rendered inside a Wails webview).

---

## Layout

Three-pane horizontal layout, full viewport height:

```
┌──────────────┬─────────────────────┬──┬─────────────────────┐
│  Sidebar     │  Editor             │  │  Preview            │
│  (resizable) │  (flex: 1)          │  │  (flex: 1)          │
│              │                     │SB│                     │
│ [Files][Git] │  Continuous scroll  │  │  Rendered HTML      │
│  File tree   │  of all files —     │  │  Auto-numbered      │
│              │  one <textarea>     │  │  scene headings     │
│  [+file]     │  per file, stacked  │  │                     │
│  [+folder]   │                     │  │                     │
│  Open…       │                     │  │                     │
│  Theme…      │                     │  │                     │
│  Save As…    │                     │  │                     │
│  Word count  │                     │  │                     │
│  Status      │                     │  │                     │
└──────────────┴─────────────────────┴──┴─────────────────────┘
        ↕ resize handle              SB = sync bar (32px)
                                          ↕ resize handle
```

Sidebar and editor each have a 4px draggable resize handle on their right edge.

## Color system

All theming is done through CSS custom properties on `:root`, set by the active theme stylesheet. Four themes ship in `themes/`:

| Theme | File | Tone |
|-------|------|------|
| Azulejo | `azulejo.css` | Cream paper, deep blue ink — Portuguese ceramic palette |
| Azulejo Dark | `azulejo-dark.css` | Inverted Azulejo |
| Calçada | `calcada.css` | Limestone cream, basalt charcoal — Portuguese paving |
| Calçada Dark | `calcada-dark.css` | Inverted Calçada |

| Variable | Use |
|----------|-----|
| `--bg` | Main background (editor, preview) |
| `--bg2` | Sidebar, headers |
| `--bg3` | Interactive elements (buttons, tree items) |
| `--bg4` | Hover states |
| `--fg` | Primary text |
| `--fg2` | Secondary text (headings in preview, dim labels) |
| `--fg3` | Tertiary text (counts, status, placeholders) |
| `--accent` | Active states, drag indicators, links |
| `--gold` | Intermezzo items |
| `--purple` | Coda items |
| `--green`, `--green2` | Primary-action accents |
| `--border` | All borders and dividers |

Themes are loaded by setting `href` on the `<link id="theme-css">` element. The selection persists in `localStorage`. User-imported themes from `~/Library/Application Support/Bacalhau/themes/` (or the platform equivalent) appear in the dropdown alongside the bundled ones.

## Sidebar header

Compact branded header containing:

- A `<canvas id="tilingCanvas">` rendering an aperiodic Penrose-rhombus tiling (Robinson triangle subdivision; ports the algorithm from `terraceonhigh/penrose-calcada`).
- App icon, "Bacalhau" title, and the current project name.

The tiling is theme-adaptive and unique on every load.

## Sidebar tabs

Two tabs: **Files** and **Git**. The Git tab shows a small badge with the count of changed files when there are any. Switching panels swaps the visible content body.

## Sidebar — File tree

The Files panel is a **recursive file tree** reflecting the project directory structure at arbitrary depth.

### Node types

| Type | Icon | Border color | Style |
|------|------|--------------|-------|
| Directory | 📁 | — | Bold, with disclosure triangle and child count |
| File | 📄 | `--border` | Normal weight |
| Read-only file | 📄 | `--border` | Italic, 60% opacity |
| Active file | — | `--accent` | 1px accent border |

### Disclosure triangles

- ▶ collapsed, ▼ expanded.
- Click the triangle to toggle (not the whole row — the row handles file open or drag start).
- Collapse state persists in `localStorage` under key `bc-collapsed`.

### Meatball menu

A **horizontal row of small buttons** that appears on hover, right-aligned within each tree item. Buttons are ~20×18px, transparent background, `--fg3` text, with hover highlight.

**On files:**
- `cp` — duplicate the file, insert copy after original in `_order.yaml`
- `rm` — delete with confirmation prompt
- 🔓 / 🔒 — toggle filesystem write permission via `chmod`

**On directories:**
- `+f` — create a new `.md` file inside this directory
- `+d` — create a new subdirectory inside this directory
- `rm` — delete directory and all contents (with confirmation)

### Insert zones (PowerPoint-style)

Between every tree item (and after the last item in each level), a **4px-tall invisible drop zone** exists. On hover it expands to ~20px and shows a thin accent-coloured line plus inline action buttons. Clicking a button creates a new chapter or directory at that position.

### Drag-and-drop

- Files and directories are both `draggable`.
- **Dragging a directory** moves the entire subtree.
- **Dropping on a directory row** inserts at the end of that directory.
- **Dropping on an insert zone** inserts at that position within the parent.
- Visual feedback: `.dragging` (opacity 0.3), `.drag-hover` (border), `.drag-into` (dashed outline).

### Click handling on draggable elements

Browsers can swallow `click` events on `draggable` elements. The fix:

```
mousedown → set didDrag = false
dragstart → set didDrag = true
mouseup   → if (!didDrag && !target.closest('.meatball')) → handle click
```

Meatball buttons use `stopPropagation()` on `mousedown` and handle actions on `mouseup`.

## Sidebar footer

Below the file tree / git panel:

- `+ file` and `+ folder` buttons (top-level creation).
- **Open…** select: open a `.bacalhau` file or the folder browser.
- **Theme** select: pick a bundled theme or import a `.css` file.
- **Save As…** select (accent-coloured, primary): `.bacalhau`, `.zip`, `.md`. (A `.pdf` entry is shown in the dropdown but the route it calls is not implemented in the current build — see `AGENTS.md`.)
- Word count line.
- Status line (11px, `--fg3`, shows last operation result).

## Editor pane

A **continuous-scroll** editor. The active file isn't shown alone — every file in the tree is rendered into its own `<textarea>` stacked vertically inside `#editorScroll`, separated by file-section headers.

- One native `<textarea>` per file — no JS editor library.
- Font: Charter / Georgia, 17px, line-height 1.7.
- Built-in browser spell check, undo/redo, find (Cmd+F).
- **Auto-save:** debounced ~1 second after last input. Status shown per section: "unsaved" → "saving…" → "saved".
- **Cmd+S:** force-save the currently focused file.
- **Read-only files:** the textarea is `readonly`, header shows italic styling and ~60% opacity.
- **Arrow keys at section edges** move the cursor into the previous/next file's textarea, so navigation feels seamless across files.

### File-section header

Above each file's textarea: filename (monospace) on the left, per-file save status on the right.

## Sync bar

A **32px-wide vertical bar** between editor and preview panes, containing three stacked buttons:

| Button | Symbol | Action |
|--------|--------|--------|
| Top | ▶ | Scroll preview to the chapter currently in the editor |
| Middle | 🔗 | Toggle linked scroll mode (highlighted when active) |
| Bottom | ◀ | Load / scroll the editor to the chapter visible in the preview |

### Linked scroll mode

When active:

- **Editor scrolls → preview follows** proportionally within the active chapter's rendered section.
- **Preview scrolls → editor follows:** detects which chapter sits at the 30% viewport mark, focuses that section in the editor, and proportionally syncs the editor scroll.
- A `syncSource` flag (`'editor'` or `'preview'`) prevents feedback loops between the two scroll listeners, cleared after a short timeout.

## Preview pane

Full manuscript rendered as one continuous HTML document, assembled from all chapter files in tree order.

### Typography

- Font: Charter / Georgia, 16px, line-height 1.65.
- Max width: 38em, centred.
- `h1`: 28px, centred, sans-serif (`Gill Sans` / `Helvetica Neue`).
- `h2` (parts): 18px, centred, uppercase, small letter-spacing, sans-serif, `--fg2`.
- `h3` (scenes): 16px, centred, italic, sans-serif, `--fg2`.
- `p`: justified, auto-hyphens.
- `hr` → centred asterisks: `∗   ∗   ∗`.
- `em`: italic. `strong`: bold. `code`: monospace with `--bg3` background.

### Chapter anchors

Each chapter's content is preceded by an invisible `<span class="chapter-anchor" id="ch-{slug}">` where the slug is the relative path with `/` and `.` replaced by `-`. Used for scroll-to on chapter select and for sync tracking.

### Markdown rendering

Client-side via the vendored `markdown-it` (`/vendor/markdown-it.min.js`).

## Welcome overlay

Shown when Bacalhau opens with no project. Three buttons: **Open .bacalhau**, **Open Folder**, **New Project**.

## Browse overlay

Folder picker. Restricted to the user's home directory.

- Breadcrumb header.
- List of subdirectories. Directories with `.md` files (or an `_order.yaml`) show a count badge — visual cue that "this looks like a Bacalhau project".
- Footer hint and **Open Here / Cancel** buttons.

## About overlay

Small modal with the app icon, name, version (fetched from `/api/version`), and a one-line description. Reachable from the menu bar (macOS) or app menu.

## Git panel

Switched in via the **Git** sidebar tab. See ARCHITECTURE.md § 2.7 for the API.

- **Status block:** modified/added/deleted/untracked files with M/A/D/? badges.
- **Stage / unstage buttons** per row and `+ all` / `− all`.
- **Commit input** with a "Commit" button.
- **History list:** last 20 commits, each with a **Restore** button.
- On non-repo projects, the panel shows an **Initialize Repository** button.
- On `.bacalhau` projects (temp extraction), the panel is hidden — committing to a doomed temp directory would be confusing.

## Filesystem conventions

- **`_order.yaml`** in each directory controls sibling order. Directories end with `/`.
- **`_part.md`** inside a directory holds the part heading content, rendered before siblings.
- Files / dirs not listed in `_order.yaml` are appended alphabetically (safe default for new files).
- Arbitrary nesting depth — the tree recurses as deep as the filesystem goes.
- Moving files between directories physically moves them on disk.
- Read-only is enforced via filesystem permissions (`chmod u-w` / `chmod u+w`).

## Principles

1. **Single binary.** Everything ships inside one Go executable: HTML, CSS, JavaScript, vendored libs, bundled themes — all `go:embed`-ed. No external runtime, no Python, no CDN, no installer for shared libraries (other than the OS webview).
2. **Filesystem is truth.** The directory structure IS the hierarchy. `_order.yaml` IS the manifest. No database, no separate config store.
3. **Everything is a file.** Chapters, part headers, ordering — all plain text on disk, all diffable, all versionable.
4. **Prose-optimized, theme-agnostic.** The editor and preview are designed for reading and writing long-form fiction; the visual register is set by the active theme, not hardcoded.
5. **Draggable elements need the mouseup pattern.** Never use `click` on `draggable` elements — use `mousedown`/`mouseup` with a drag flag.
