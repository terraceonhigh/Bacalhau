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
import webbrowser
import urllib.parse

# Set by main() from command-line argument
CHAPTERS_DIR = None


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


def get_themes_dir():
    """Return the themes/ directory next to chapters/. Create if absent."""
    themes = os.path.join(os.path.dirname(CHAPTERS_DIR), "themes")
    if not os.path.isdir(themes):
        os.makedirs(themes, exist_ok=True)
    return themes


def list_themes():
    """List available .css theme files."""
    themes_dir = get_themes_dir()
    return sorted(f for f in os.listdir(themes_dir) if f.endswith(".css"))


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
button {
  padding: 6px 14px; border: 1px solid var(--border); border-radius: 3px;
  background: var(--bg3); color: var(--fg); cursor: pointer;
  font-size: 12px; transition: background 0.1s; width: 100%;
}
button:hover { background: var(--bg4); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover { opacity: 0.85; }

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
  <div class="tree" id="tree"></div>
  <div class="sidebar-footer">
    <select id="themeSelect" onchange="switchTheme(this.value)" style="width:100%;padding:5px;background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:3px;font-size:12px;">
      <option value="">No theme</option>
    </select>
    <button onclick="newFile('')">+ New Chapter</button>
    <button onclick="newDir('')">+ New Folder</button>
    <button onclick="saveProject()">Save .zip</button>
    <button class="primary" id="exportBtn" onclick="exportMarkdown()">Export .md</button>
    <div class="status" id="status"></div>
  </div>
</div>

<div class="editor-pane" id="editorPane">
  <div class="editor-scroll" id="editorScroll"></div>
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
  renderTree();
  await buildEditor();
  renderPreview();
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
          '<span class="mb" title="Rename" data-action="rn">rn</span>' +
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
          if (action === 'rn') startInlineRename(row, node.path, node.name, 'dir');
          else if (action === 'cpdir') copyDir(node.path);
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
          '<span class="mb" title="Rename" data-action="rn">rn</span>' +
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
          if (action === 'rn') startInlineRename(row, node.path, node.name, 'file');
          else if (action === 'cp') copyFile(node.path);
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
    });
    ta.addEventListener('focus', () => {
      activeFile = f.path;
      renderTree();
      highlightActiveHeader();
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
  const midY = rect.top + rect.height * 0.3;
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
  let editorSyncTimer = null;
  editorScroll.addEventListener('scroll', () => {
    if (selectGuard) return;
    // Update active file immediately on every scroll
    const visible = getVisibleEditorFile();
    const fileChanged = visible && visible !== activeFile;
    if (fileChanged) {
      activeFile = visible;
      renderTree();
      highlightActiveHeader();
    }
    // Sync to preview if linked — debounce slightly to avoid feedback loops
    // and skip the tick where activeFile just changed (prevents jumping)
    if (syncLinked && syncSource !== 'preview' && !fileChanged) {
      clearTimeout(editorSyncTimer);
      editorSyncTimer = setTimeout(syncEditorToPreview, 16);
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
  document.getElementById('exportBtn').disabled = true;
  const r = await fetch('/api/export/markdown');
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bone-china.md';
  a.click();
  URL.revokeObjectURL(url);
  setStatus('Downloaded bone-china.md');
  document.getElementById('exportBtn').disabled = false;
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
  if (!activeFile) return;
  const editorScroll = document.getElementById('editorScroll');
  const pane = document.getElementById('previewPane');
  if (!editorScroll || !pane) return;

  // Find the active file's section in the editor and its range in the preview
  const section = document.querySelector('.file-section[data-path="' + activeFile + '"]');
  const slug = activeFile.replace(/[\/\\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (!section || !anchor) return;

  // How far through this file's editor section are we?
  const sectionTop = section.offsetTop;
  const sectionBot = sectionTop + section.offsetHeight;
  const viewMid = editorScroll.scrollTop + editorScroll.clientHeight * 0.3;
  const ratio = section.offsetHeight > 0
    ? Math.max(0, Math.min(1, (viewMid - sectionTop) / section.offsetHeight))
    : 0;

  // Find the preview range for this file (anchor to next anchor)
  const allAnchors = pane.querySelectorAll('.chapter-anchor');
  let previewTop = anchor.offsetTop;
  let previewBot = pane.scrollHeight;
  let found = false;
  for (const a of allAnchors) {
    if (found) { previewBot = a.offsetTop; break; }
    if (a === anchor) found = true;
  }
  const previewHeight = previewBot - previewTop;

  // Map the ratio to the preview position
  const target = previewTop + ratio * previewHeight - pane.clientHeight * 0.3;
  syncSource = 'editor';
  pane.scrollTop = Math.max(0, target);
  setTimeout(() => { syncSource = null; }, 300);
}

function syncPreviewToEditor() {
  if (!activeFile) return;
  const editorScroll = document.getElementById('editorScroll');
  const pane = document.getElementById('previewPane');
  if (!editorScroll || !pane) return;

  const section = document.querySelector('.file-section[data-path="' + activeFile + '"]');
  const slug = activeFile.replace(/[\/\\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (!section || !anchor) return;

  // How far through this file's preview range are we?
  const allAnchors = pane.querySelectorAll('.chapter-anchor');
  let previewTop = anchor.offsetTop;
  let previewBot = pane.scrollHeight;
  let found = false;
  for (const a of allAnchors) {
    if (found) { previewBot = a.offsetTop; break; }
    if (a === anchor) found = true;
  }
  const previewHeight = previewBot - previewTop;
  const viewMid = pane.scrollTop + pane.clientHeight * 0.3;
  const ratio = previewHeight > 0
    ? Math.max(0, Math.min(1, (viewMid - previewTop) / previewHeight))
    : 0;

  // Map the ratio to the editor section
  const target = section.offsetTop + ratio * section.offsetHeight - editorScroll.clientHeight * 0.3;
  syncSource = 'preview';
  editorScroll.scrollTop = Math.max(0, target);
  setTimeout(() => { syncSource = null; }, 300);
}

function getVisibleChapter() {
  const pane = document.getElementById('previewPane');
  const midY = pane.getBoundingClientRect().top + pane.clientHeight * 0.3;
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

let previewScrollTimer = null;
document.getElementById('previewPane').addEventListener('scroll', () => {
  if (!syncLinked || syncSource === 'editor' || selectGuard) return;
  clearTimeout(previewScrollTimer);
  previewScrollTimer = setTimeout(() => {
    if (selectGuard) return;
    const visible = getVisibleChapter();
    const fileChanged = visible && visible !== activeFile;
    if (fileChanged) {
      activeFile = visible;
      renderTree();
      highlightActiveHeader();
    }
    // Only sync position if we didn't just cross a file boundary
    if (!fileChanged) syncPreviewToEditor();
  }, 100);
});

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function setStatus(msg) { document.getElementById('status').textContent = msg; }

document.addEventListener('keydown', e => {
  if ((e.metaKey||e.ctrlKey) && e.key === 's') { e.preventDefault(); saveFile(); }
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
  // Restore saved theme
  const saved = localStorage.getItem('bc-theme') || '';
  if (saved && data.themes.includes(saved)) {
    select.value = saved;
    applyTheme(saved);
  }
}

function switchTheme(name) {
  localStorage.setItem('bc-theme', name);
  applyTheme(name);
}

function applyTheme(name) {
  const link = document.getElementById('theme-css');
  link.href = name ? '/api/themes/' + encodeURIComponent(name) : '';
}

loadTree();
loadThemes();
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
        elif self.path == "/api/save/zip":
            self.save_zip()
        elif self.path == "/api/themes":
            self.serve_themes_list()
        elif self.path.startswith("/api/themes/"):
            self.serve_theme_css()
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
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
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
        themes_dir = get_themes_dir()
        filepath = os.path.join(themes_dir, name)
        if not os.path.exists(filepath) or not name.endswith(".css"):
            self.send_json(404, {"error": "Theme not found"})
            return
        with open(filepath, "r") as f:
            css = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.end_headers()
        self.wfile.write(css.encode())

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global CHAPTERS_DIR

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

    if not project_dir:
        # Default: look for chapters/ next to the script, fall back to script dir
        script_dir = os.path.dirname(os.path.abspath(__file__)) or "."
        chapters_subdir = os.path.join(script_dir, "chapters")
        if not os.path.isdir(chapters_subdir):
            os.makedirs(chapters_subdir)
        project_dir = chapters_subdir

    CHAPTERS_DIR = os.path.abspath(project_dir)
    if not os.path.isdir(CHAPTERS_DIR):
        print(f"Error: not a directory: {CHAPTERS_DIR}", file=sys.stderr)
        sys.exit(1)

    pid = os.getpid()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Bacalhau: {url} — editing {CHAPTERS_DIR}")
    print(f"PID: {pid} — kill with: kill {pid}")
    print("Press Ctrl+C to stop.")

    def shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down.")
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
