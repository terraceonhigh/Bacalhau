// ── Bacalhau Web Edition ─────────────────────────────────────────────────────
// Fully client-side — no backend. .bacalhau files (ZIP) are opened/saved via
// the browser File API + JSZip. All state lives in memory.

// ── In-memory project state ─────────────────────────────────────────────────
let tree = [];           // [{type:'file'|'dir', name, path, heading, writable, children?}]
let files = {};          // path -> content (markdown strings)
let projectName = '';
let activeFile = null;
let collapsed = JSON.parse(localStorage.getItem('bc-collapsed') || '{}');
let dragItem = null;
let _bacalhauFileHandle = null;

// ── Confirm dialog ──────────────────────────────────────────────────────────
function bcConfirm(msg) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:99999';
    const card = document.createElement('div');
    card.style.cssText = 'background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:20px;max-width:400px;box-shadow:0 4px 20px rgba(0,0,0,0.3)';
    card.innerHTML = '<p style="color:var(--fg);margin:0 0 16px;font-size:13px;">' + msg.replace(/</g,'&lt;') + '</p>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end">'
      + '<button id="bcConfirmNo" style="padding:6px 16px;cursor:pointer">Cancel</button>'
      + '<button id="bcConfirmYes" style="padding:6px 16px;cursor:pointer;font-weight:bold">OK</button></div>';
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    const cleanup = (val) => { document.body.removeChild(overlay); resolve(val); };
    card.querySelector('#bcConfirmYes').onclick = () => cleanup(true);
    card.querySelector('#bcConfirmNo').onclick = () => cleanup(false);
    overlay.onclick = (e) => { if (e.target === overlay) cleanup(false); };
  });
}

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function setStatus(msg) { document.getElementById('status').textContent = msg; }

// ── Markdown parser ─────────────────────────────────────────────────────────
const _md = window.markdownit({ html: false, linkify: true, typographer: true });
function md(text) { return _md.render(text); }

// ── Aperiodic tiling ────────────────────────────────────────────────────────
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

// ── Tree helpers ─────────────────────────────────────────────────────────────

// Build tree from flat files map. Paths like "part-1/01-chapter.md" become
// nested dir/file nodes. Sorting: dirs by name, then files by name.
function buildTreeFromFiles() {
  const root = [];

  function ensureDir(parts) {
    let level = root;
    let path = '';
    for (const part of parts) {
      path = path ? path + '/' + part : part;
      let existing = level.find(n => n.type === 'dir' && n.name === part);
      if (!existing) {
        existing = { type: 'dir', name: part, path, heading: headingFromName(part), children: [] };
        level.push(existing);
      }
      level = existing.children;
    }
    return level;
  }

  for (const filePath of Object.keys(files).sort()) {
    const parts = filePath.split('/');
    const fname = parts.pop();
    const parent = parts.length > 0 ? ensureDir(parts) : root;
    const content = files[filePath];
    // Extract heading from first # or ## or ### line, or use filename
    const headingMatch = content.match(/^#{1,3}\s+(.+)$/m);
    const heading = headingMatch ? headingMatch[1].trim() : fname.replace(/\.md$/, '');
    parent.push({
      type: 'file',
      name: fname,
      path: filePath,
      heading,
      writable: true
    });
  }

  // Sort each level: dirs first (by name), then files (by name)
  function sortLevel(nodes) {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { numeric: true });
    });
    for (const n of nodes) {
      if (n.type === 'dir' && n.children) sortLevel(n.children);
    }
  }
  sortLevel(root);
  return root;
}

function headingFromName(name) {
  return name.replace(/^\d+-/, '').replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function flatFiles(nodes) {
  const result = [];
  for (const n of nodes) {
    if (n.type === 'file') result.push(n);
    else if (n.children) result.push(...flatFiles(n.children));
  }
  return result;
}

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

function countFiles(node) {
  if (node.type === 'file') return 1;
  return (node.children||[]).reduce((s,c) => s + countFiles(c), 0);
}

// ── Load tree + editor + preview from in-memory state ────────────────────────
function loadTree() {
  tree = buildTreeFromFiles();
  const pn = document.getElementById('projectName');
  if (pn) pn.textContent = projectName;
  document.getElementById('welcomeOverlay').style.display = 'none';
  renderTree();
  buildEditor();
  renderPreview();
  updateWordCount();
}

// ── Tree rendering ──────────────────────────────────────────────────────────
function renderTree() {
  const container = document.getElementById('tree');
  container.innerHTML = '';
  let sceneNum = 0;
  assignSceneNumbers(tree, () => ++sceneNum);
  const ul = buildTreeUL(tree, '');
  container.appendChild(ul);
}

function assignSceneNumbers(nodes, nextNum) {
  for (const node of nodes) {
    if (node.type === 'dir') {
      assignSceneNumbers(node.children || [], nextNum);
    } else {
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
  zone.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; zone.classList.add('drag-hover'); });
  zone.addEventListener('dragleave', () => { zone.classList.remove('drag-hover'); });
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-hover'); onDrop(parentPath, position); });
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

      row.querySelector('.toggle').addEventListener('click', e => {
        e.stopPropagation();
        collapsed[node.path] = !collapsed[node.path];
        localStorage.setItem('bc-collapsed', JSON.stringify(collapsed));
        renderTree();
      });

      row.querySelector('.label').addEventListener('dblclick', e => {
        e.stopPropagation();
        startInlineRename(row, node.path, node.name, 'dir');
      });

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

      row.addEventListener('dragstart', e => { dragItem = {type:'dir', path:node.path}; e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); });
      row.addEventListener('dragend', () => { clearAllDragState(); dragItem=null; });
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
  ul.appendChild(makeInsertZone(parentPath, nodes.length));
  return ul;
}

// ── Drag and drop ────────────────────────────────────────────────────────────
function clearAllDragState() {
  document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));
  document.querySelectorAll('.drag-into').forEach(el => el.classList.remove('drag-into'));
  document.querySelectorAll('.drag-hover').forEach(el => el.classList.remove('drag-hover'));
}

function onDrop(targetDir, position) {
  if (!dragItem) return;
  if (dragItem.path === targetDir) return;
  if (dragItem.type === 'dir' && targetDir.startsWith(dragItem.path + '/')) return;

  // Move in files map: rename all paths that start with dragItem.path
  const srcPath = dragItem.path;
  const srcName = srcPath.split('/').pop();
  const newPath = targetDir ? targetDir + '/' + srcName : srcName;

  if (srcPath === newPath) return;

  const newFiles = {};
  for (const [p, content] of Object.entries(files)) {
    if (p === srcPath || p.startsWith(srcPath + '/')) {
      const newP = newPath + p.slice(srcPath.length);
      newFiles[newP] = content;
    } else {
      newFiles[p] = content;
    }
  }
  files = newFiles;
  if (activeFile && (activeFile === srcPath || activeFile.startsWith(srcPath + '/'))) {
    activeFile = newPath + activeFile.slice(srcPath.length);
  }
  setStatus('Moved ' + srcName);
  loadTree();
}

// ── Editor ──────────────────────────────────────────────────────────────────
let selectGuard = false;
let fileSaveTimers = {};
let fileDirtyFlags = {};

function buildEditor() {
  const allFiles = flatFiles(tree);
  const container = document.getElementById('editorScroll');
  container.innerHTML = '';
  fileSaveTimers = {};
  fileDirtyFlags = {};

  for (const f of allFiles) {
    const section = document.createElement('div');
    section.className = 'file-section';
    section.dataset.path = f.path;

    const header = document.createElement('div');
    header.className = 'file-header' + (activeFile === f.path ? ' active' : '');
    header.dataset.path = f.path;
    const fname = f.path.split('/').pop();
    header.innerHTML = '<span>' + esc(fname) + (f.writable ? '' : ' (read-only)') + '</span><span class="save-indicator" id="save-' + esc(f.path.replace(/[\/\.]/g, '-')) + '"></span>';
    header.addEventListener('click', () => {
      activeFile = f.path;
      renderTree();
      highlightActiveHeader();
    });
    section.appendChild(header);

    const ta = document.createElement('textarea');
    ta.value = files[f.path] || '';
    ta.readOnly = !f.writable;
    ta.spellcheck = true;
    ta.dataset.path = f.path;

    function autoResize() {
      ta.style.height = 'auto';
      ta.style.height = ta.scrollHeight + 'px';
    }
    ta.addEventListener('input', () => {
      autoResize();
      // Save to in-memory store immediately
      files[f.path] = ta.value;
      fileDirtyFlags[f.path] = true;
      const ind = document.getElementById('save-' + f.path.replace(/[\/\.]/g, '-'));
      if (ind) { ind.textContent = '\u25CF'; ind.className = 'save-indicator unsaved'; }
      clearTimeout(fileSaveTimers[f.path]);
      fileSaveTimers[f.path] = setTimeout(() => markSaved(f.path), 1000);
      schedulePreviewUpdate();
      updateWordCount();
      // Update heading in tree
      const headingMatch = ta.value.match(/^#{1,3}\s+(.+)$/m);
      const node = findNode(tree, f.path);
      if (node) node.heading = headingMatch ? headingMatch[1].trim() : fname.replace(/\.md$/, '');
      renderTree();
      // Persist to localStorage
      saveToLocalStorage();
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
        const prev = ta.closest('.file-section').previousElementSibling;
        if (prev) {
          const prevTa = prev.querySelector('textarea');
          if (prevTa) { e.preventDefault(); prevTa.focus(); prevTa.selectionStart = prevTa.selectionEnd = prevTa.value.length; }
        }
      } else if (e.key === 'ArrowDown' && pos === val.length) {
        const next = ta.closest('.file-section').nextElementSibling;
        if (next) {
          const nextTa = next.querySelector('textarea');
          if (nextTa) { e.preventDefault(); nextTa.focus(); nextTa.selectionStart = nextTa.selectionEnd = 0; }
        }
      }
    });
    section.appendChild(ta);
    container.appendChild(section);
    requestAnimationFrame(autoResize);
  }
}

function markSaved(path) {
  fileDirtyFlags[path] = false;
  const ind = document.getElementById('save-' + path.replace(/[\/\.]/g, '-'));
  if (ind) { ind.textContent = 'saved'; ind.className = 'save-indicator'; }
  setTimeout(() => { if (!fileDirtyFlags[path] && ind) ind.textContent = ''; }, 2000);
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

function selectFile(path) {
  selectGuard = true;
  setTimeout(() => { selectGuard = false; }, 800);
  activeFile = path;
  renderTree();
  highlightActiveHeader();
  const section = document.querySelector('.file-section[data-path="' + path + '"]');
  if (section) section.scrollIntoView({behavior:'smooth', block:'start'});
  const slug = path.replace(/[\/\.]/g, '-');
  const anchor = document.getElementById('ch-' + slug);
  if (anchor) anchor.scrollIntoView({behavior:'smooth', block:'start'});
}

// ── Preview ──────────────────────────────────────────────────────────────────
let previewRafPending = false;

function renderPreview() {
  const container = document.getElementById('preview');
  const allFiles = flatFiles(tree);
  let html = '';
  let sceneNum = 0;
  for (const f of allFiles) {
    const slug = f.path.replace(/[\/\.]/g, '-');
    html += '<span class="chapter-anchor" id="ch-'+esc(slug)+'"></span>';
    const fname = f.path.split('/').pop();
    const isScene = fname !== '_part.md' && !fname.startsWith('intermezzo-') && fname !== 'title.md';
    let content = files[f.path] || '';
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
    renderPreview();
    previewRafPending = false;
  });
}

// ── File operations (in-memory) ──────────────────────────────────────────────
function newFile(dir, position) {
  let slug = 'untitled';
  let num = 1;
  const makePath = (s) => dir ? dir + '/' + s + '.md' : s + '.md';
  while (files[makePath(slug)]) {
    slug = 'untitled-' + num;
    num++;
  }
  const path = makePath(slug);
  files[path] = '### Untitled\n\n';
  activeFile = path;
  loadTree();
  saveToLocalStorage();
  setTimeout(() => {
    const row = document.querySelector('[data-path="'+path+'"]');
    if (row) startInlineRename(row, path, slug + '.md', 'file');
  }, 50);
}

function newDir(parentDir, position) {
  let name = 'untitled';
  let num = 1;
  const makePath = (n) => parentDir ? parentDir + '/' + n : n;
  // Check if dir exists by checking if any file starts with this path
  while (Object.keys(files).some(p => p.startsWith(makePath(name) + '/'))) {
    name = 'untitled-' + num;
    num++;
  }
  const path = makePath(name);
  // Create a placeholder file inside
  files[path + '/untitled.md'] = '### Untitled\n\n';
  loadTree();
  saveToLocalStorage();
  setTimeout(() => {
    const row = document.querySelector('[data-path="'+path+'"]');
    if (row) startInlineRename(row, path, name, 'dir');
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
    if (!newName || newName === baseName) { loadTree(); return; }
    renameItem(path, newName, type);
  }
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { done = true; renaming = false; loadTree(); }
    e.stopPropagation();
  });
  input.addEventListener('blur', commit);
  input.addEventListener('mousedown', e => e.stopPropagation());
}

function renameItem(path, newName, type) {
  const parts = path.split('/');
  parts.pop();
  const newLast = type === 'file' ? newName + '.md' : newName;
  const newPath = parts.length > 0 ? parts.join('/') + '/' + newLast : newLast;

  const newFiles = {};
  for (const [p, content] of Object.entries(files)) {
    if (type === 'file' && p === path) {
      newFiles[newPath] = content;
    } else if (type === 'dir' && (p === path + '/' || p.startsWith(path + '/'))) {
      const newP = newPath + p.slice(path.length);
      newFiles[newP] = content;
    } else {
      newFiles[p] = content;
    }
  }
  files = newFiles;
  if (activeFile === path) activeFile = newPath;
  else if (activeFile && activeFile.startsWith(path + '/')) activeFile = newPath + activeFile.slice(path.length);
  setStatus('Renamed to ' + newName);
  loadTree();
  saveToLocalStorage();
}

async function copyFile(path) {
  const parts = path.split('/');
  const fname = parts.pop().replace(/\.md$/, '');
  const dir = parts.join('/');
  let newName = fname + '-copy.md';
  let num = 2;
  const makePath = () => dir ? dir + '/' + newName : newName;
  while (files[makePath()]) {
    newName = fname + '-copy-' + num + '.md';
    num++;
  }
  files[makePath()] = files[path] || '';
  setStatus('Copied');
  loadTree();
  saveToLocalStorage();
}

async function removeFile(path) {
  if (!await bcConfirm('Delete ' + path + '?')) return;
  delete files[path];
  if (activeFile === path) activeFile = null;
  setStatus('Deleted');
  loadTree();
  saveToLocalStorage();
}

function copyDir(path) {
  let newPath = path + '-copy';
  let num = 2;
  while (Object.keys(files).some(p => p.startsWith(newPath + '/'))) {
    newPath = path + '-copy-' + num;
    num++;
  }
  for (const [p, content] of Object.entries(files)) {
    if (p.startsWith(path + '/')) {
      files[newPath + p.slice(path.length)] = content;
    }
  }
  setStatus('Copied folder');
  loadTree();
  saveToLocalStorage();
}

async function removeDir(path) {
  if (!await bcConfirm('Delete folder ' + path + ' and all contents?')) return;
  for (const p of Object.keys(files)) {
    if (p.startsWith(path + '/')) delete files[p];
  }
  if (activeFile && activeFile.startsWith(path + '/')) activeFile = null;
  setStatus('Deleted folder');
  loadTree();
  saveToLocalStorage();
}

function toggleLock(path) {
  const node = findNode(tree, path);
  if (node) {
    node.writable = !node.writable;
    setStatus(node.writable ? 'Unlocked' : 'Locked');
    loadTree();
  }
}

// ── Open .bacalhau file (ZIP) ────────────────────────────────────────────────
function handleOpen(value) {
  if (value === 'bacalhau') {
    if (window.showOpenFilePicker) {
      window.showOpenFilePicker({
        types: [{description: 'Bacalhau project', accept: {'application/octet-stream': ['.bacalhau']}}]
      }).then(([handle]) => {
        _bacalhauFileHandle = handle;
        return handle.getFile();
      }).then(file => openBacalhauFile(file))
      .catch(e => {
        if (e.name === 'AbortError') return;
        document.getElementById('openInput').click();
      });
    } else {
      document.getElementById('openInput').click();
    }
  }
}

function handleOpenFile(input) {
  const file = input.files[0];
  if (!file) return;
  _bacalhauFileHandle = null;
  openBacalhauFile(file);
  input.value = '';
}

async function openBacalhauFile(file) {
  setStatus('Opening ' + file.name + '...');
  try {
    const zip = await JSZip.loadAsync(file);
    files = {};
    projectName = file.name.replace(/\.bacalhau$/, '');

    // Extract chapters/ directory
    const promises = [];
    zip.forEach((relativePath, zipEntry) => {
      if (zipEntry.dir) return;
      // Only extract chapters/ files (skip .git/, latex/, etc.)
      if (!relativePath.startsWith('chapters/')) return;
      // Strip "chapters/" prefix
      const path = relativePath.slice('chapters/'.length);
      if (!path || !path.endsWith('.md')) return;
      promises.push(
        zipEntry.async('string').then(content => {
          files[path] = content;
        })
      );
    });
    await Promise.all(promises);

    if (Object.keys(files).length === 0) {
      setStatus('No markdown files found in archive');
      return;
    }

    loadTree();
    setStatus('Opened ' + file.name + ' (' + Object.keys(files).length + ' files)');
    saveToLocalStorage();
  } catch(e) {
    setStatus('Open failed: ' + e.message);
  }
}

// ── Save .bacalhau file (ZIP) ────────────────────────────────────────────────
function handleSaveAs(fmt) {
  if (fmt === 'bacalhau') saveBacalhau();
  else if (fmt === 'md') exportMarkdown();
}

async function saveBacalhau() {
  setStatus('Saving .bacalhau...');
  try {
    const zip = new JSZip();
    const chaptersFolder = zip.folder('chapters');

    for (const [path, content] of Object.entries(files)) {
      // Recreate directory structure inside chapters/
      chaptersFolder.file(path, content);
    }

    const blob = await zip.generateAsync({type: 'blob'});
    const filename = (projectName || 'project') + '.bacalhau';

    // Try File System Access API for in-place save
    if (_bacalhauFileHandle) {
      try {
        const writable = await _bacalhauFileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        setStatus('Saved to ' + _bacalhauFileHandle.name);
        return;
      } catch(e) {
        // Fall through to download
      }
    }

    // Browser download fallback
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    setStatus('Saved ' + filename);
  } catch(e) {
    setStatus('Save failed: ' + e.message);
  }
}

async function exportMarkdown() {
  const allFiles = flatFiles(tree);
  let text = '';
  for (const f of allFiles) {
    text += (files[f.path] || '') + '\n\n';
  }
  const blob = new Blob([text], {type: 'text/markdown'});
  const filename = (projectName || 'manuscript') + '.md';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  setStatus('Exported ' + filename);
}

// ── New project ──────────────────────────────────────────────────────────────
function startNewProject() {
  files = { 'title.md': '# My Manuscript\n\n', '01-chapter.md': '### Chapter One\n\n' };
  projectName = 'New Project';
  activeFile = null;
  loadTree();
  saveToLocalStorage();
}

// ── LocalStorage persistence ─────────────────────────────────────────────────
function saveToLocalStorage() {
  try {
    const data = { files, projectName };
    localStorage.setItem('bc-project', JSON.stringify(data));
  } catch(e) {
    // Storage full or unavailable — silently ignore
  }
}

function loadFromLocalStorage() {
  try {
    const raw = localStorage.getItem('bc-project');
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (data.files && Object.keys(data.files).length > 0) {
      files = data.files;
      projectName = data.projectName || '';
      return true;
    }
  } catch(e) {}
  return false;
}

// ── Word count ───────────────────────────────────────────────────────────────
function countWords(text) {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

function updateWordCount() {
  const el = document.getElementById('wordCount');
  if (!el) return;
  let fileWords = 0;
  if (activeFile && files[activeFile]) {
    fileWords = countWords(files[activeFile]);
  }
  let totalWords = 0;
  for (const content of Object.values(files)) {
    totalWords += countWords(content);
  }
  const parts = [];
  if (activeFile) parts.push('File: ' + fileWords.toLocaleString());
  parts.push('Total: ' + totalWords.toLocaleString());
  el.textContent = parts.join(' \u2022 ');
}

// ── Keyboard shortcuts ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if ((e.metaKey||e.ctrlKey) && e.key === 's') { e.preventDefault(); saveBacalhau(); }
  if ((e.metaKey||e.ctrlKey) && e.key === 'o') { e.preventDefault(); handleOpen('bacalhau'); }
});

// ── Themes ───────────────────────────────────────────────────────────────────
const BUNDLED_THEMES = ['azulejo.css', 'azulejo-dark.css', 'calcada.css', 'calcada-dark.css'];

function loadThemes() {
  const select = document.getElementById('themeSelect');
  select.innerHTML = '<option value="">No theme</option>';
  for (const name of BUNDLED_THEMES) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name.replace('.css', '');
    select.appendChild(opt);
  }
  const saved = localStorage.getItem('bc-theme') || '';
  if (saved && BUNDLED_THEMES.includes(saved)) {
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
  link.href = name ? 'themes/' + encodeURIComponent(name) : '';
  if (name) {
    link.onload = () => renderTiling();
  } else {
    setTimeout(renderTiling, 50);
  }
}

// ── Sync ─────────────────────────────────────────────────────────────────────
let syncLinked = false;
let syncSource = null;

function syncEditorToPreview() {
  const editorScroll = document.getElementById('editorScroll');
  const pane = document.getElementById('previewPane');
  if (!editorScroll || !pane) return;

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

  const viewY = pane.scrollTop + pane.clientHeight * 0.5;
  const allAnchors = Array.from(pane.querySelectorAll('.chapter-anchor'));
  let anchorIdx = 0;
  for (let i = allAnchors.length - 1; i >= 0; i--) {
    if (allAnchors[i].offsetTop <= viewY) { anchorIdx = i; break; }
  }

  const anchor = allAnchors[anchorIdx];
  if (!anchor) return;
  const previewTop = anchor.offsetTop;
  const previewBot = anchorIdx + 1 < allAnchors.length ? allAnchors[anchorIdx + 1].offsetTop : pane.scrollHeight;
  const previewHeight = previewBot - previewTop;
  const localRatio = previewHeight > 0 ? (viewY - previewTop) / previewHeight : 0;

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
    if (d < bestDist) { bestDist = d; best = a.id.replace('ch-', ''); }
  }
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

// Editor scroll listener
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

// Preview scroll listener
document.getElementById('previewPane').addEventListener('scroll', () => {
  if (!syncLinked || syncSource === 'editor' || selectGuard) return;
  const visible = getVisibleChapter();
  const fileChanged = visible && visible !== activeFile;
  if (fileChanged) {
    activeFile = visible;
    renderTree();
    highlightActiveHeader();
  }
  if (!fileChanged) {
    requestAnimationFrame(() => { syncPreviewToEditor(); });
  }
});

// ── Sidebar panel switcher ───────────────────────────────────────────────────
function switchPanel(panel) {
  document.getElementById('tree').style.display = panel === 'files' ? '' : 'none';
  document.querySelectorAll('.sidebar-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.panel === panel);
  });
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

  initResize('resizeSidebar',
    () => sidebar.offsetWidth,
    (w) => { sidebar.style.width = w + 'px'; sidebar.style.minWidth = w + 'px'; },
    180
  );

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

// ── Initialize ───────────────────────────────────────────────────────────────
loadThemes();

// Try to restore previous session from localStorage
if (loadFromLocalStorage()) {
  loadTree();
  setStatus('Restored previous session');
} else {
  // Show welcome overlay (already visible via HTML)
}

setTimeout(renderTiling, 500);
