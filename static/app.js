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

// ── Header menu ──────────────────────────────────────────────────────────────
function toggleHeaderMenu() {
  const menu = document.getElementById('headerMenu');
  menu.style.display = menu.style.display === 'none' ? '' : 'none';
}
document.addEventListener('click', (e) => {
  const menu = document.getElementById('headerMenu');
  if (menu && !e.target.closest('.header-menu-wrap')) menu.style.display = 'none';
});
async function showAbout() {
  document.getElementById('headerMenu').style.display = 'none';
  try {
    const data = await api('/api/version');
    document.getElementById('aboutVersion').textContent = data.version || 'dev';
  } catch(e) {
    document.getElementById('aboutVersion').textContent = 'dev';
  }
  document.getElementById('aboutOverlay').style.display = 'flex';
}

// ── Aperiodic tiling ─────────────────────────────────────────────────────────
// Adapted from https://github.com/terraceonhigh/penrose-calcada (1e75b4e)
// Penrose P3 via Robinson triangle subdivision. Upstream is the interactive
// standalone version; this is a minimal embedding for the sidebar header.
// To update: port changes from penrose-calcada/index.html, then bump the hash.
// Functions prefixed with _ to avoid global collisions (subdivide → _penroseSubdivide, etc).
const _PHI = (1 + Math.sqrt(5)) / 2;

function _penroseSubdivide(triangles) {
  const result = [];
  for (const [type, ax, ay, bx, by, cx, cy] of triangles) {
    if (type === 0) {
      const px = ax + (bx - ax) / _PHI, py = ay + (by - ay) / _PHI;
      result.push([0, cx, cy, px, py, bx, by]);
      result.push([1, px, py, cx, cy, ax, ay]);
    } else {
      const qx = bx + (ax - bx) / _PHI, qy = by + (ay - by) / _PHI;
      const rx = bx + (cx - bx) / _PHI, ry = by + (cy - by) / _PHI;
      result.push([1, rx, ry, cx, cy, ax, ay]);
      result.push([1, qx, qy, rx, ry, bx, by]);
      result.push([0, rx, ry, qx, qy, ax, ay]);
    }
  }
  return result;
}

function _pairRhombi(triangles) {
  const edgeMap = new Map();
  const paired = new Set();
  const rhombi = [];
  function ek(x1, y1, x2, y2) {
    const a = x1.toFixed(4)+','+y1.toFixed(4), b = x2.toFixed(4)+','+y2.toFixed(4);
    return a < b ? a+'|'+b : b+'|'+a;
  }
  for (let i = 0; i < triangles.length; i++) {
    const [type, ax, ay, bx, by, cx, cy] = triangles[i];
    const key = ek(bx, by, cx, cy);
    if (edgeMap.has(key)) {
      const j = edgeMap.get(key);
      const [, ax2, ay2] = triangles[j];
      rhombi.push({type, verts:[[ax,ay],[bx,by],[ax2,ay2],[cx,cy]], idx:rhombi.length});
      paired.add(i); paired.add(j);
    } else { edgeMap.set(key, i); }
  }
  for (let i = 0; i < triangles.length; i++) {
    if (!paired.has(i)) {
      const [type, ax, ay, bx, by, cx, cy] = triangles[i];
      rhombi.push({type, verts:[[ax,ay],[bx,by],[cx,cy]], idx:rhombi.length, unpaired:true});
    }
  }
  return rhombi;
}

function renderTiling() {
  const canvas = document.getElementById('tilingCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const parent = canvas.parentElement;
  const cw = parent.offsetWidth;
  const ch = parent.offsetHeight;
  if (cw === 0 || ch === 0) return;
  canvas.width = cw * dpr;
  canvas.height = ch * dpr;
  canvas.style.width = cw + 'px';
  canvas.style.height = ch + 'px';
  const W = canvas.width;
  const H = canvas.height;

  // Generate triangles
  const rot = Math.random() * Math.PI * 2;
  const R = Math.max(W, H) * 2;
  let triangles = [];
  for (let i = 0; i < 10; i++) {
    const a1 = rot + (2*i-1) * Math.PI/10, a2 = rot + (2*i+1) * Math.PI/10;
    const bx = R*Math.cos(a1), by = R*Math.sin(a1);
    const cx = R*Math.cos(a2), cy = R*Math.sin(a2);
    triangles.push(i%2===0 ? [0,0,0,cx,cy,bx,by] : [0,0,0,bx,by,cx,cy]);
  }
  for (let s = 0; s < 6; s++) triangles = _penroseSubdivide(triangles);

  // Pair into rhombi and pick colouring mode
  const rhombi = _pairRhombi(triangles);
  const colorMode = Math.random() < 0.5 ? 'alt' : 'type';

  const style = getComputedStyle(document.documentElement);
  const accent = style.getPropertyValue('--accent').trim() || '#5b9bd5';
  const bg3 = style.getPropertyValue('--bg3').trim() || '#222';
  const border = style.getPropertyValue('--border').trim() || '#333';

  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(W/2, H/2);

  for (const r of rhombi) {
    ctx.fillStyle = colorMode === 'alt'
      ? (r.idx % 2 === 0 ? accent : bg3)
      : (r.type === 0 ? accent : bg3);
    ctx.strokeStyle = border;
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(r.verts[0][0], r.verts[0][1]);
    for (let k = 1; k < r.verts.length; k++) ctx.lineTo(r.verts[k][0], r.verts[k][1]);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

// ── Markdown parser (markdown-it) ────────────────────────────────────────────
const _md = window.markdownit({ html: false, linkify: true, typographer: true });
function md(text) { return _md.render(text); }

// ── Tree rendering ───────────────────────────────────────────────────────────
async function loadTree() {
  const data = await api('/api/tree');
  tree = data.tree;
  const pn = document.getElementById('projectName');
  if (pn) pn.textContent = data.project || '';
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
        '<span class="toggle">'+(isCollapsed ? '\u25B6' : '\u25BC')+'</span>' +
        '<span class="icon">\uD83D\uDCC1</span>' +
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
      const lockIcon = node.writable ? '\uD83D\uDD13' : '\uD83D\uDD12';
      row.innerHTML =
        '<span class="toggle"></span>' +
        '<span class="icon">\uD83D\uDCC4</span>' +
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
    header.innerHTML = '<span>' + esc(fname) + (writable ? '' : ' (read-only)') + '</span><span class="save-indicator" id="save-' + esc(f.path.replace(/[\/\.]/g, '-')) + '"></span>';
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
      const ind = document.getElementById('save-' + f.path.replace(/[\/\.]/g, '-'));
      if (ind) { ind.textContent = '\u25CF'; ind.className = 'save-indicator unsaved'; }
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

function resizeAllTextareas() {
  document.querySelectorAll('.file-section textarea').forEach(ta => {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  });
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
  const slug = path.replace(/[\/\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (anchor) anchor.scrollIntoView({behavior:'smooth', block:'start'});
}

async function saveFileByPath(path) {
  if (!fileDirtyFlags[path]) return;
  const ta = document.querySelector('textarea[data-path="' + path + '"]');
  if (!ta) return;
  const content = ta.value;
  const ind = document.getElementById('save-' + path.replace(/[\/\.]/g, '-'));
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
    const slug = path.replace(/[\/\.]/g, '-');
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
    const slug = f.path.replace(/[\/\.]/g, '-');
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
  // Auto-stage: keep all changes staged by default
  if (gitState.is_repo && gitState.files && gitState.files.some(f => !f.staged)) {
    await api('/api/git/stage', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({all: true})});
    try { gitState = await api('/api/git/status'); } catch(e) {}
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
    el.innerHTML = '<div class="git-message">No repository found.<br><br><button class="primary" onclick="gitInit()" style="width:auto;padding:8px 20px;">Initialize Repository</button></div>';
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
    html += '<button class="git-action" onclick="gitUnstage(\'' + esc(f.path).replace(/'/g, "\\'") + '\')" title="Unstage">\u2212</button>';
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
    html += '<button class="git-action" onclick="gitStage(\'' + esc(f.path).replace(/'/g, "\\'") + '\')" title="Stage">+</button>';
    html += '</div>';
  }

  // Commit area
  html += '<div class="git-commit-area">';
  html += '<input type="text" id="gitCommitMsg" placeholder="Commit message" onkeydown="if(event.key===\'Enter\')gitCommit()">';
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
      html += '<button class="git-action" onclick="gitRestore(\'' + esc(c.sha) + '\')" title="Restore to this version">restore</button>';
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
  const baseName = type === 'file' ? currentName.replace(/\.md$/, '') : currentName;
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

let _bacalhauFileHandle = null;  // File System Access API handle for overwrite

async function saveBacalhau() {
  setStatus('Saving .bacalhau…');
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
      // Try to overwrite the original file via File System Access API
      if (_bacalhauFileHandle) {
        try {
          const writable = await _bacalhauFileHandle.createWritable();
          await writable.write(blob);
          await writable.close();
          setStatus('Saved ' + _bacalhauFileHandle.name);
          return;
        } catch(e) {
          // Permission revoked or API error — fall through to download
        }
      }
      // Try showSaveFilePicker for a native save dialog
      if (window.showSaveFilePicker) {
        try {
          const disp = r.headers.get('Content-Disposition') || '';
          const match = disp.match(/filename="?([^"]+)"?/);
          const suggestedName = match ? match[1] : 'project.bacalhau';
          const handle = await window.showSaveFilePicker({
            suggestedName,
            types: [{description: 'Bacalhau project', accept: {'application/octet-stream': ['.bacalhau']}}]
          });
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
          _bacalhauFileHandle = handle;
          setStatus('Saved ' + handle.name);
          return;
        } catch(e) {
          if (e.name === 'AbortError') { setStatus('Save cancelled'); return; }
          // Fall through to legacy download
        }
      }
      // Legacy fallback: browser download
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

async function handleOpen(value) {
  if (value === 'bacalhau') {
    // Try File System Access API first — gives us a handle for overwriting later
    if (window.showOpenFilePicker) {
      try {
        const [handle] = await window.showOpenFilePicker({
          types: [{description: 'Bacalhau project', accept: {'application/octet-stream': ['.bacalhau']}}]
        });
        const file = await handle.getFile();
        _bacalhauFileHandle = handle;
        await openBacalhauFile(file);
        return;
      } catch(e) {
        if (e.name === 'AbortError') return;  // User cancelled
        // Fall through to legacy input
      }
    }
    document.getElementById('openInput').click();
  } else if (value === 'folder') {
    openBrowse();
  }
}

async function handleOpenFile(input) {
  const file = input.files[0];
  if (!file) return;
  _bacalhauFileHandle = null;  // Legacy input — no handle for overwrite
  await openBacalhauFile(file);
  input.value = '';
}

async function openBacalhauFile(file) {
  setStatus('Opening ' + file.name + '…');
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
    const rel = browseData.path.slice(homePath.length).replace(/^\//, '');
    const segments = rel.split('/');
    for (let i = 0; i < segments.length; i++) {
      const segPath = homePath + '/' + segments.slice(0, i + 1).join('/');
      html += '<span class="sep"> / </span><span onclick="browseTo(\'' + esc(segPath).replace(/'/g, "\\'") + '\')">' + esc(segments[i]) + '</span>';
    }
  }
  bc.innerHTML = html;

  // Directory list
  const list = document.getElementById('browseList');
  html = '';

  // Parent directory link
  if (browseData.parent) {
    html += '<div class="browse-item" onclick="browseTo(\'' + esc(browseData.parent).replace(/'/g, "\\'") + '\')">';
    html += '<span class="browse-icon">\u2190</span>';
    html += '<span class="browse-name" style="color:var(--fg2)">..</span>';
    html += '</div>';
  }

  if (browseData.entries.length === 0 && !browseData.parent) {
    html += '<div class="browse-empty">This folder is empty</div>';
  }

  for (const e of browseData.entries) {
    const cls = e.isProject ? 'browse-item is-project' : 'browse-item';
    const entryPath = browseData.path + '/' + e.name;
    html += '<div class="' + cls + '" onclick="browseTo(\'' + esc(entryPath).replace(/'/g, "\\'") + '\')">';
    html += '<span class="browse-icon">\uD83D\uDCC1</span>';
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
  setStatus('Opening folder\u2026');
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
  setStatus('Generating PDF…');
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
  const slug = path.replace(/[\/\.]/g, '-');
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

  // Find which file section is at the 50% viewport mark, and how far through it
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
  const slug = path.replace(/[\/\.]/g, '-');
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

  // Find which preview chapter is at the 50% viewport mark
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
    s.dataset.path.replace(/[\/\.]/g, '-') === slug
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
    if (n.type === 'file' && n.path.replace(/[\/\.]/g, '-') === slug) return n.path;
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
  document.getElementById('syncIcon').textContent = syncLinked ? '\u{1F517}' : '\u{26D3}';
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
  return trimmed.split(/\s+/).length;
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
  el.textContent = parts.join(' \u2022 ');
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
  sep.textContent = '────────';
  select.appendChild(sep);
  const imp = document.createElement('option');
  imp.value = '__import__';
  imp.textContent = 'Import theme…';
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
  setStatus('Importing theme…');
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
  // Re-render tiling with new theme colours after stylesheet loads
  if (name) {
    link.onload = () => renderTiling();
  } else {
    setTimeout(renderTiling, 50);
  }
}

loadTree();
loadThemes();
setTimeout(renderTiling, 500);

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

// ── Auto-resize textareas on pane/window resize ──────────────────────────────
let resizeRafPending = false;
function scheduleTextareaResize() {
  if (resizeRafPending) return;
  resizeRafPending = true;
  requestAnimationFrame(() => { resizeAllTextareas(); resizeRafPending = false; });
}
window.addEventListener('resize', scheduleTextareaResize);
if (typeof ResizeObserver !== 'undefined') {
  const ep = document.getElementById('editorPane');
  if (ep) new ResizeObserver(scheduleTextareaResize).observe(ep);
}

// ── Resizable panes ──────────────────────────────────────────────────────────
(function() {
  function initResize(handleId, getTarget, setSize, minSize) {
    const handle = document.getElementById(handleId);
    if (!handle) return;
    let startX, startSize;
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startX = e.clientX;
      startSize = getTarget();
      handle.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      function onMove(e) {
        const delta = e.clientX - startX;
        const newSize = Math.max(minSize, startSize + delta);
        setSize(newSize);
      }
      function onUp() {
        handle.classList.remove('active');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  const sidebar = document.querySelector('.sidebar');
  const editorPane = document.getElementById('editorPane');
  const previewPane = document.getElementById('previewPane');

  // Sidebar resize
  initResize('resizeSidebar',
    () => sidebar.offsetWidth,
    (w) => { sidebar.style.width = w + 'px'; sidebar.style.minWidth = w + 'px'; },
    180
  );

  // Editor/Preview resize — adjust flex-basis
  initResize('resizeEditor',
    () => editorPane.offsetWidth,
    (w) => {
      editorPane.style.flex = 'none';
      editorPane.style.width = w + 'px';
      previewPane.style.flex = '1';
    },
    200
  );
})();
