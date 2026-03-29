# Editor Design Conventions

> UI patterns and interaction design for the Bone China browser editor (`scripts/editor.py`).

---

## Layout

Three-pane horizontal layout, full viewport height:

```
┌──────────────┬─────────────────────┬──┬─────────────────────┐
│  Sidebar     │  Editor             │  │  Preview            │
│  (300px)     │  (flex: 1)          │  │  (flex: 1)          │
│              │                     │SB│                     │
│  File tree   │  <textarea>         │  │  Rendered HTML      │
│              │                     │  │                     │
│  [+ New]     │                     │  │                     │
│  [+ Folder]  │                     │  │                     │
│  [Export]    │                     │  │                     │
└──────────────┴─────────────────────┴──┴─────────────────────┘
                                      SB = sync bar (32px)
```

## Color System

Dark theme. CSS variables on `:root`:

| Variable | Value | Use |
|----------|-------|-----|
| `--bg` | `#111` | Main background (editor, preview) |
| `--bg2` | `#1a1a1a` | Sidebar, headers |
| `--bg3` | `#222` | Interactive elements (buttons, tree items) |
| `--bg4` | `#2a2a2a` | Hover states |
| `--fg` | `#e0e0e0` | Primary text |
| `--fg2` | `#aaa` | Secondary text (headings in preview) |
| `--fg3` | `#666` | Tertiary text (counts, status, placeholders) |
| `--accent` | `#5b9bd5` | Active states, drag indicators, links |
| `--gold` | `#b59b5b` | Intermezzo items |
| `--purple` | `#9b5bb5` | Coda items |
| `--green` / `--green2` | `#1a5a1a` / `#2a7a2a` | Primary action buttons |
| `--border` | `#333` | All borders and dividers |

## Sidebar — File Tree

The sidebar is a **recursive file tree** reflecting the `chapters/` directory structure at arbitrary depth.

### Node types

| Type | Icon | Border color | Style |
|------|------|-------------|-------|
| Directory | 📁 | — | Bold, with disclosure triangle and child count |
| File (scene) | 📄 | `--border` | Normal weight |
| Read-only file | 📄 | `--border` | Italic, 60% opacity |
| Active file | — | `--accent` | 1px accent border |

### Disclosure triangles

- ▶ collapsed, ▼ expanded
- Click the triangle to toggle (not the whole row — the row opens the editor or starts a drag)
- Collapse state persisted in `localStorage` under key `bc-collapsed`

### Meatball menu

A **horizontal row of small buttons** that appears on hover, right-aligned within each tree item. Buttons are 20x18px, transparent background, `--fg3` text, with hover highlight.

**On files:**
- `cp` — duplicate the file, insert copy after original in `_order.yaml`
- `rm` — delete with confirmation prompt
- 🔓/🔒 — toggle filesystem write permission via `chmod`

**On directories:**
- `+f` — create a new `.md` file inside this directory
- `+d` — create a new subdirectory inside this directory
- `rm` — delete directory and all contents (with confirmation)

### Insert zones (PowerPoint-style)

Between every tree item (and after the last item in each level), a **4px tall invisible drop zone** exists. On hover it expands to show a thin accent-colored line. Clicking it creates a new chapter at that position.

### Drag-and-drop

- Files and directories are both `draggable`
- **Dragging a directory** moves the entire subtree
- **Dropping on a directory row** inserts at the end of that directory
- **Dropping on an insert zone** inserts at that position within the parent
- Visual feedback: `.dragging` (opacity 0.3), `.drag-over` (top border), `.drag-into` (dashed outline)

### Click handling on draggable elements

Browsers can swallow `click` events on `draggable` elements. The fix:

```
mousedown → set didDrag = false
dragstart → set didDrag = true
mouseup   → if (!didDrag && !target.closest('.meatball')) → handle click
```

Meatball buttons use `stopPropagation()` on `mousedown` and handle actions on `mouseup`.

## Editor Pane

- Native `<textarea>` — no JS editor library
- Font: Charter / Georgia, 17px, line-height 1.7
- Padding: 24px 32px
- Built-in browser spell check, undo/redo, find (Cmd+F)
- **Auto-save**: debounced 1 second after last input. Status shown in header: "unsaved" → "saving..." → "saved" (clears after 2s)
- **Cmd+S**: force immediate save
- **Read-only files**: textarea is `disabled`, header shows "(read-only)"

### Editor header

Left: filename (monospace). Right: save status (11px, `--fg3`).

## Sync Bar

A **32px-wide vertical bar** between editor and preview panes, containing three stacked buttons:

| Button | Symbol | Action |
|--------|--------|--------|
| Top | ▶ | Scroll preview to the chapter currently in the editor |
| Middle | 🔗 | Toggle linked scroll mode (blue when active) |
| Bottom | ◀ | Load the chapter visible in the preview into the editor |

### Linked scroll mode

When active (🔗 button is blue):

- **Editor scrolls → preview follows** proportionally within the active chapter's rendered section
- **Preview scrolls → editor follows**: detects which chapter is at the 30% viewport mark, auto-loads it (if editor is not dirty), and proportionally syncs the editor scroll position
- A `syncSource` flag (`'editor'` or `'preview'`) prevents feedback loops between the two scroll listeners, cleared after 50ms

## Preview Pane

Full manuscript rendered as one continuous HTML document, assembled from all chapter files in tree order.

### Typography

- Font: Charter / Georgia, 16px, line-height 1.65
- Max width: 38em, centered
- `h1`: 28px, centered, sans-serif
- `h2` (parts): 18px, centered, uppercase, small letter-spacing, sans-serif, `--fg2`
- `h3` (scenes): 16px, centered, italic, sans-serif, `--fg2`
- `p`: justified, auto-hyphens
- `hr` → centered asterisks: `∗   ∗   ∗` (Unicode ∗ with em-space)
- `em`: italic. `strong`: bold. `code`: monospace with `--bg3` background

### Chapter anchors

Each chapter's content is preceded by an invisible `<span class="chapter-anchor" id="ch-{slug}">` where the slug is the relative path with `/` and `.` replaced by `-`. Used for scroll-to on chapter select and for sync tracking.

### Markdown rendering

Client-side, inline JS (~20 lines). Handles:
- Headings (`#` through `######`)
- Paragraphs (double newline separated)
- Bold (`**text**`), italic (`*text*`), inline code (`` `text` ``)
- Horizontal rules (`---`, `***`)

No external library. Sufficient for prose fiction.

## Footer

Bottom of the sidebar. Three stacked full-width buttons:

1. **+ New Chapter** — creates a file at the top level of `chapters/`
2. **+ New Folder** — creates a directory at the top level
3. **Export PDF** (green/primary) — triggers Pandoc pipeline, shows status

Status line below: 11px, `--fg3`, shows last operation result.

## Filesystem Conventions

- **`_order.yaml`** in each directory controls sibling order. Directories end with `/`.
- **`_part.md`** inside a part directory holds the part heading content.
- Files/dirs not listed in `_order.yaml` are appended alphabetically (safe default for new files).
- Arbitrary nesting depth — the tree recurses as deep as the filesystem goes.
- Moving files between directories physically moves them on disk.
- Read-only is enforced via filesystem permissions (`chmod u-w` / `chmod u+w`).

## Principles

1. **Zero dependencies at runtime.** Python stdlib only. No CDN, no npm, no pip. The editor is one Python file with inline HTML/CSS/JS.
2. **Filesystem is truth.** The directory structure IS the hierarchy. `_order.yaml` IS the manifest. No database, no complex config.
3. **Everything is a file.** Chapters, part headers, ordering — all plain text on disk, all diffable, all versionable.
4. **Dark theme, prose-optimized.** The editor and preview are designed for reading and writing long-form fiction, not code.
5. **Draggable elements need the mouseup pattern.** Never use `click` on `draggable` elements — use `mousedown`/`mouseup` with a drag flag.
