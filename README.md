# Bacalhau

A browser-based manuscript editor. Zero dependencies. One Python file.

---

## What this is

A three-pane editor for long-form writing projects organized as hierarchical markdown files:

- **Left:** File tree sidebar with drag-and-drop reordering, inline rename, and collapse/expand
- **Center:** Markdown editor with auto-save and spell check
- **Right:** Live preview of the full manuscript with auto-numbering

## Usage

```bash
python3 editor.py <project-directory>
python3 editor.py <project-directory> --port 8080
```

Opens the editor at `localhost:3000`. The project directory should contain `.md` files and/or subdirectories. Each directory can have an `_order.yaml` file to control sibling order.

## Project structure

A Bacalhau project is just a directory of markdown files:

```
my-novel/
  _order.yaml          # controls top-level order
  title.md
  part-one/
    _order.yaml         # controls order within this part
    _part.md            # part heading (## Part One)
    chapter-one.md
    chapter-two.md
  part-two/
    _order.yaml
    _part.md
    chapter-three.md
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

Optional file inside a directory containing a section heading (e.g., `## Part One`). Rendered before the directory's other files.

## Assembly

```bash
python3 assemble.py my-novel/ --concat -o my-novel.md
python3 assemble.py my-novel/ --latex -o my-novel.tex --templates latex/
python3 assemble.py my-novel/ --pdf -o my-novel.pdf --templates latex/
```

Concatenates all files in tree order into a single output file. Scene headings (`### Title`) are auto-numbered sequentially, skipping `_part.md` files and files prefixed with `intermezzo-`.

## Features

- **Zero dependencies.** Python 3 stdlib only. No npm, no pip, no CDN.
- **Single file.** The editor is one Python file with inline HTML/CSS/JS.
- **Filesystem is truth.** The directory structure IS the hierarchy. `_order.yaml` files control ordering.
- **Auto-save.** Editor saves 1 second after you stop typing. `Cmd+S` to force.
- **Drag-and-drop.** Reorder files and directories. Drag a directory to move the entire subtree.
- **Inline rename.** Double-click a label or use the `rn` button.
- **File operations.** Copy, delete, read-only toggle (via filesystem permissions) on hover.
- **Auto-numbering.** Scene headings are numbered by position, not stored in the files.
- **Save/Export.** Download the project as a `.zip` or the assembled manuscript as `.md`.
- **Linked scroll.** Proportional scroll sync between editor and preview.
- **Arbitrary depth.** Nest directories as deep as you like.

## Files

| File | What it is |
|------|-----------|
| `editor.py` | The editor — serves the browser UI and file API |
| `assemble.py` | Concatenates a project into a single markdown/LaTeX/PDF |
| `DESIGN.md` | UI patterns and interaction conventions |

## Design

See `DESIGN.md` for the full UI specification: layout, color system, drag-and-drop conventions, sync bar, and the mouseup pattern for draggable elements.
