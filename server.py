"""HTTP request handler for the Bacalhau editor."""

import base64
import http.server
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile

import state
from helpers import (
    read_order,
    write_order,
    build_tree,
    walk_files,
    resolve_path,
    get_heading,
    _git_root,
    _run_git,
    _git_installed,
    _git_has_commits,
    _git_resolve_path,
    list_themes,
    find_theme,
    _user_themes_dir,
    _repack_bacalhau,
    _read_order_raw,
)


class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.serve_html()
        elif self.path == "/favicon.png":
            self.serve_favicon()
        elif self.path.startswith("/static/"):
            self.serve_static()
        elif self.path.startswith("/vendor/"):
            self.serve_vendor_file()
        elif self.path == "/api/tree":
            self.serve_tree()
        elif self.path.startswith("/api/chapter/"):
            self.serve_chapter()
        elif self.path == "/api/preview":
            self.serve_preview()
        elif self.path == "/api/export/markdown":
            self.export_markdown()
        elif self.path == "/api/export/pdf":
            self.export_pdf()
        elif self.path == "/api/save/zip":
            self.save_zip()
        elif self.path == "/api/save/bacalhau":
            self.save_bacalhau()
        elif self.path == "/api/themes":
            self.serve_themes_list()
        elif self.path == "/api/heartbeat":
            state._last_heartbeat = time.time()
            self.send_json(200, {"ok": True})
        elif self.path.startswith("/api/themes/"):
            self.serve_theme_css()
        elif self.path == "/api/git/status":
            self.git_status()
        elif self.path == "/api/git/log":
            self.git_log()
        elif self.path.startswith("/api/browse"):
            self.browse_directory()
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
        elif self.path == "/api/shutdown":
            _repack_bacalhau()
            self.send_json(200, {"ok": True})
            threading.Timer(0.5, lambda: os._exit(0)).start()
        elif self.path == "/api/open":
            self.open_project()
        elif self.path == "/api/themes/import":
            self.import_theme()
        elif self.path == "/api/git/init":
            self.git_init()
        elif self.path == "/api/git/stage":
            self.git_stage()
        elif self.path == "/api/git/unstage":
            self.git_unstage()
        elif self.path == "/api/git/commit":
            self.git_commit()
        elif self.path == "/api/git/restore":
            self.git_restore()
        elif self.path == "/api/open/folder":
            self.open_folder()
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(script_dir, "static", "index.html")
        with open(html_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self):
        name = self.path.split("/static/", 1)[1]
        name = urllib.parse.unquote(name)
        if ".." in name or name.startswith("/"):
            self.send_json(400, {"error": "Invalid path"})
            return
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, "static", name)
        if not os.path.isfile(filepath):
            self.send_json(404, {"error": "Not found"})
            return
        mime_types = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
        ext = os.path.splitext(name)[1]
        ct = mime_types.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def serve_favicon(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon = os.path.join(script_dir, "icons", "icon.png")
        if not os.path.isfile(icon):
            icon = os.path.join(script_dir, "icon.png")
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

    def serve_vendor_file(self):
        name = self.path.split("/vendor/", 1)[1]
        name = urllib.parse.unquote(name)
        if "/" in name or name.startswith("."):
            self.send_json(400, {"error": "Invalid path"})
            return
        vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
        filepath = os.path.join(vendor_dir, name)
        if not os.path.isfile(filepath):
            self.send_json(404, {"error": "Not found"})
            return
        ct = "application/javascript" if name.endswith(".js") else "application/octet-stream"
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def serve_tree(self):
        t = build_tree(state.CHAPTERS_DIR)
        # Derive project name from .bacalhau filename or directory name
        if state.BACALHAU_NAME:
            pname = state.BACALHAU_NAME.replace('.bacalhau', '')
        elif state.BACALHAU_FILE:
            pname = os.path.basename(state.BACALHAU_FILE).replace('.bacalhau', '')
        else:
            d = state.CHAPTERS_DIR
            if d and os.path.basename(d) == 'chapters':
                d = os.path.dirname(d)
            pname = os.path.basename(d) if d else ''
        self.send_json(200, {"tree": t, "project": pname})

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
        for filepath in walk_files(state.CHAPTERS_DIR):
            relpath = os.path.relpath(filepath, state.CHAPTERS_DIR)
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

        new_relpath = os.path.relpath(new_abs, state.CHAPTERS_DIR)
        self.send_json(200, {
            "message": f"Renamed \u2192 {new_name}",
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
        dirpath = resolve_path(parent_dir) if parent_dir else state.CHAPTERS_DIR
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
        relpath = os.path.relpath(filepath, state.CHAPTERS_DIR)
        self.send_json(200, {"path": relpath, "fname": fname, "message": f"Created {relpath}"})

    def new_dir(self):
        body = self.read_body()
        name = re.sub(r"[^a-z0-9-]", "-", body.get("name", "").lower()).strip("-")
        parent_dir = body.get("dir", "")
        auto = body.get("autoIncrement", False)
        if not name:
            self.send_json(400, {"error": "Invalid name"})
            return
        dirpath = resolve_path(parent_dir) if parent_dir else state.CHAPTERS_DIR
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
        dest_abs = resolve_path(dest_dir) if dest_dir else state.CHAPTERS_DIR

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
            self.send_json(200, {"writable": False, "message": f"{relpath} \u2192 read-only"})
        else:
            os.chmod(abspath, current | stat.S_IWUSR)
            self.send_json(200, {"writable": True, "message": f"{relpath} \u2192 writable"})

    def export_markdown(self):
        """Concatenate chapters with scene numbers and serve as a download."""
        parts = []
        counter = [0]
        for filepath in walk_files(state.CHAPTERS_DIR):
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

    def export_pdf(self):
        """Generate PDF from assembled markdown (pure Python, no external deps)."""
        # Assemble markdown with scene numbers
        parts = []
        counter = [0]
        for filepath in walk_files(state.CHAPTERS_DIR):
            with open(filepath, "r") as f:
                content = f.read()
            basename = os.path.basename(filepath)
            if basename != "_part.md" and not basename.startswith("intermezzo-") and basename != "title.md":
                counter[0] += 1
                n = counter[0]
                content = re.sub(r"^(### )(.+)$", rf"\g<1>{n}. \2", content, count=1, flags=re.MULTILINE)
            parts.append(content)
        text = "".join(parts)
        try:
            vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
            if vendor_dir not in sys.path:
                sys.path.insert(0, vendor_dir)
            from md2pdf import markdown_to_pdf_bytes
            data = markdown_to_pdf_bytes(text)
        except Exception as e:
            self.send_json(500, {"error": f"PDF generation failed: {str(e)[:500]}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="bone-china.pdf"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def save_zip(self):
        """Zip the entire chapters/ directory and serve as a download."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(state.CHAPTERS_DIR):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    filepath = os.path.join(root, fname)
                    arcname = os.path.relpath(filepath, state.CHAPTERS_DIR)
                    zf.write(filepath, os.path.join("chapters", arcname))
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="bone-china-chapters.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def save_bacalhau(self):
        """Save project as .bacalhau (zip with custom extension)."""
        if state.BACALHAU_FILE:
            # In-place save: repack to original file
            _repack_bacalhau()
            self.send_json(200, {"message": "Saved", "path": os.path.basename(state.BACALHAU_FILE)})
        else:
            # Download mode: build zip and serve
            buf = io.BytesIO()
            project_root = os.path.dirname(state.CHAPTERS_DIR)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(state.CHAPTERS_DIR):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fname in sorted(files):
                        if fname.startswith("."):
                            continue
                        filepath = os.path.join(root, fname)
                        arcname = os.path.relpath(filepath, state.CHAPTERS_DIR)
                        zf.write(filepath, os.path.join("chapters", arcname))
                latex_dir = os.path.join(project_root, "latex")
                if os.path.isdir(latex_dir):
                    for root2, dirs2, files2 in os.walk(latex_dir):
                        dirs2[:] = [d for d in dirs2 if not d.startswith(".")]
                        for fname in sorted(files2):
                            if fname.startswith("."):
                                continue
                            filepath = os.path.join(root2, fname)
                            arcname = os.path.relpath(filepath, latex_dir)
                            zf.write(filepath, os.path.join("latex", arcname))
                # Bundle .git so version history travels with the file
                git_dir = os.path.join(project_root, ".git")
                if os.path.isdir(git_dir):
                    for root3, dirs3, files3 in os.walk(git_dir):
                        for fname in files3:
                            filepath = os.path.join(root3, fname)
                            arcname = os.path.relpath(filepath, project_root)
                            zf.write(filepath, arcname)
            data = buf.getvalue()
            name = state.BACALHAU_NAME or (os.path.basename(project_root) + ".bacalhau")
            if not name.endswith(".bacalhau"):
                name += ".bacalhau"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def open_project(self):
        """Open a .bacalhau file uploaded from the browser."""
        body = self.read_body()
        if not body or "data" not in body:
            self.send_json(400, {"error": "Missing file data"})
            return
        try:
            raw = base64.b64decode(body["data"])
        except Exception:
            self.send_json(400, {"error": "Invalid file data"})
            return
        # Save current project if it's a .bacalhau
        _repack_bacalhau()
        # Clean up old temp dir
        old_temp = state.TEMP_DIR
        # Extract to new temp dir
        new_temp = tempfile.mkdtemp(prefix="bacalhau-")
        tmp_file = os.path.join(new_temp, "upload.bacalhau")
        with open(tmp_file, "wb") as f:
            f.write(raw)
        try:
            with zipfile.ZipFile(tmp_file, "r") as zf:
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(new_temp, member))
                    if not target.startswith(os.path.realpath(new_temp) + os.sep) and target != os.path.realpath(new_temp):
                        self.send_json(400, {"error": f"Unsafe path in archive: {member}"})
                        shutil.rmtree(new_temp, ignore_errors=True)
                        return
                zf.extractall(new_temp)
            os.unlink(tmp_file)
        except zipfile.BadZipFile:
            self.send_json(400, {"error": "Not a valid .bacalhau file"})
            shutil.rmtree(new_temp, ignore_errors=True)
            return
        chapters_path = os.path.join(new_temp, "chapters")
        if not os.path.isdir(chapters_path):
            self.send_json(400, {"error": "No chapters/ directory in file"})
            shutil.rmtree(new_temp, ignore_errors=True)
            return
        # Switch to new project
        state.CHAPTERS_DIR = chapters_path
        state.BACALHAU_FILE = None  # Uploaded copy — no disk path
        state.BACALHAU_NAME = body.get("filename", "project.bacalhau")
        state.TEMP_DIR = new_temp
        # Clean up old temp
        if old_temp and os.path.isdir(old_temp):
            shutil.rmtree(old_temp, ignore_errors=True)
        self.send_json(200, {"ok": True, "name": body.get("filename", "project.bacalhau")})

    # ── Folder browser ──────────────────────────────────────────────────────────

    def browse_directory(self):
        """List subdirectories for the folder browser."""
        home = os.path.expanduser("~")
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        req_path = qs.get("path", [None])[0]
        target = os.path.realpath(req_path) if req_path else home
        # Security: restrict to home directory
        if not target.startswith(home):
            self.send_json(403, {"error": "Access denied"})
            return
        if not os.path.isdir(target):
            self.send_json(404, {"error": "Directory not found"})
            return
        try:
            raw = os.listdir(target)
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
            return
        # Filter to visible directories only
        dirs = []
        for name in sorted(raw, key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if not os.path.isdir(full):
                continue
            # Count .md files and check for _order.yaml
            try:
                children = os.listdir(full)
            except PermissionError:
                children = []
            md_count = sum(1 for c in children if c.endswith(".md"))
            is_project = "_order.yaml" in children or md_count > 0
            dirs.append({"name": name, "isProject": is_project, "mdCount": md_count})
            if len(dirs) >= 200:
                break
        # Current directory info
        try:
            cur_children = os.listdir(target)
        except PermissionError:
            cur_children = []
        cur_md = sum(1 for c in cur_children if c.endswith(".md"))
        cur_is_project = "_order.yaml" in cur_children or cur_md > 0
        parent = os.path.dirname(target)
        if not parent.startswith(home):
            parent = None
        self.send_json(200, {
            "path": target,
            "home": home,
            "parent": parent,
            "atHome": target == home,
            "isProject": cur_is_project,
            "mdCount": cur_md,
            "entries": dirs,
        })

    def open_folder(self):
        """Switch to a local directory as the project."""
        home = os.path.expanduser("~")
        body = self.read_body()
        req_path = (body.get("path") or "").strip()
        if not req_path:
            self.send_json(400, {"error": "No path specified"})
            return
        target = os.path.realpath(req_path)
        if not target.startswith(home):
            self.send_json(403, {"error": "Access denied"})
            return
        if not os.path.isdir(target):
            self.send_json(404, {"error": "Directory not found"})
            return
        # Save current project if it's a .bacalhau
        _repack_bacalhau()
        old_temp = state.TEMP_DIR
        state.CHAPTERS_DIR = target
        state.BACALHAU_FILE = None
        state.TEMP_DIR = None
        if old_temp and os.path.isdir(old_temp):
            shutil.rmtree(old_temp, ignore_errors=True)
        self.send_json(200, {"ok": True, "path": target})

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
        filepath = find_theme(name)
        if not filepath or not name.endswith(".css"):
            self.send_json(404, {"error": "Theme not found"})
            return
        with open(filepath, "r") as f:
            css = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.end_headers()
        self.wfile.write(css.encode())

    def import_theme(self):
        """Import a user-uploaded CSS theme file."""
        body = self.read_body()
        if not body or "data" not in body or "filename" not in body:
            self.send_json(400, {"error": "Missing file data"})
            return
        name = body["filename"]
        if not name.endswith(".css") or "/" in name or name.startswith("."):
            self.send_json(400, {"error": "Invalid theme filename"})
            return
        try:
            raw = base64.b64decode(body["data"])
        except Exception:
            self.send_json(400, {"error": "Invalid file data"})
            return
        dest = os.path.join(_user_themes_dir(), name)
        with open(dest, "wb") as f:
            f.write(raw)
        self.send_json(200, {"ok": True, "name": name})

    # ── Git endpoints ──────────────────────────────────────────────────────────

    def git_status(self):
        installed = _git_installed()
        root = _git_root()
        is_temp = bool(state.TEMP_DIR and state.CHAPTERS_DIR and state.CHAPTERS_DIR.startswith(state.TEMP_DIR))
        if not installed:
            self.send_json(200, {"git_installed": False, "is_repo": False, "is_temp": is_temp, "files": []})
            return
        if not root:
            self.send_json(200, {"git_installed": True, "is_repo": False, "is_temp": is_temp, "files": []})
            return
        # Scope status to CHAPTERS_DIR (and its parent project dir) so we
        # don't show unrelated files when the project is inside a larger repo
        scope = state.CHAPTERS_DIR
        parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent  # include project-level files too (e.g. _order.yaml)
        rc, out, err = _run_git("status", "--porcelain=v1", "-uall", "--", scope)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Compute relative prefix so we can show short paths
        rel_prefix = ""
        if root and scope and scope.startswith(root):
            rel_prefix = os.path.relpath(scope, root)
            if rel_prefix == ".":
                rel_prefix = ""
        files = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path = line[3:]
            # Handle renamed files (old -> new)
            if " -> " in path:
                path = path.split(" -> ")[-1]
            # Strip the scope prefix for cleaner display
            if rel_prefix and path.startswith(rel_prefix + "/"):
                path = path[len(rel_prefix) + 1:]
            if index_status not in (" ", "?"):
                files.append({"path": path, "status": index_status, "staged": True})
            if worktree_status not in (" ", ""):
                st = "?" if worktree_status == "?" else worktree_status
                files.append({"path": path, "status": st, "staged": False})
        self.send_json(200, {"git_installed": True, "is_repo": True, "is_temp": is_temp, "files": files})

    def git_init(self):
        # Prefer parent of chapters/ as the repo root
        root = state.CHAPTERS_DIR
        if root and os.path.basename(root) == "chapters":
            root = os.path.dirname(root)
        rc, out, err = _run_git("init", cwd=root)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_stage(self):
        body = self.read_body()
        if body.get("all"):
            # Stage all within the project scope
            scope = state.CHAPTERS_DIR
            parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
            root = _git_root()
            if parent and parent != root:
                scope = parent
            rc, out, err = _run_git("add", "--", scope)
        else:
            path = body.get("path", "")
            if not path:
                self.send_json(400, {"error": "No path specified"})
                return
            rc, out, err = _run_git("add", "--", _git_resolve_path(path))
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_unstage(self):
        body = self.read_body()
        has_commits = _git_has_commits()
        if body.get("all"):
            # Unstage all within the project scope
            scope = state.CHAPTERS_DIR
            parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
            root = _git_root()
            if parent and parent != root:
                scope = parent
            if has_commits:
                rc, out, err = _run_git("reset", "HEAD", "--", scope)
            else:
                rc, out, err = _run_git("rm", "--cached", "-r", "--", scope)
        else:
            path = body.get("path", "")
            if not path:
                self.send_json(400, {"error": "No path specified"})
                return
            full_path = _git_resolve_path(path)
            if has_commits:
                rc, out, err = _run_git("reset", "HEAD", "--", full_path)
            else:
                rc, out, err = _run_git("rm", "--cached", "--", full_path)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        self.send_json(200, {"ok": True})

    def git_commit(self):
        body = self.read_body()
        msg = (body.get("message") or "").strip()
        if not msg:
            self.send_json(400, {"error": "Commit message required"})
            return
        rc, out, err = _run_git("commit", "-m", msg)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Extract short SHA from output like "[main abc1234] message"
        sha = ""
        m = re.search(r'\[[\w/.-]+ ([a-f0-9]+)\]', out)
        if m:
            sha = m.group(1)
        self.send_json(200, {"ok": True, "sha": sha})

    def git_log(self):
        if not _git_installed() or not _git_root():
            self.send_json(200, {"commits": []})
            return
        # Scope log to the project directory
        scope = state.CHAPTERS_DIR
        root = _git_root()
        parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent
        rc, out, err = _run_git(
            "log", "--format=%H\t%h\t%s\t%ar", "-20", "--", scope
        )
        if rc != 0:
            self.send_json(200, {"commits": []})
            return
        commits = []
        for line in out.strip().splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0],
                    "short": parts[1],
                    "message": parts[2],
                    "when": parts[3],
                })
        self.send_json(200, {"commits": commits})

    def git_restore(self):
        body = self.read_body()
        sha = (body.get("sha") or "").strip()
        if not sha:
            self.send_json(400, {"error": "No commit specified"})
            return
        if not _git_has_commits():
            self.send_json(400, {"error": "No commits to restore from"})
            return
        # Scope restore to the project directory
        scope = state.CHAPTERS_DIR
        root = _git_root()
        parent = os.path.dirname(state.CHAPTERS_DIR) if state.CHAPTERS_DIR else None
        if parent and parent != root:
            scope = parent
        # Checkout all project files from that commit
        rc, out, err = _run_git("checkout", sha, "--", scope)
        if rc != 0:
            self.send_json(500, {"error": err.strip()})
            return
        # Find the original commit message for the auto-commit
        rc2, msg_out, _ = _run_git("log", "--format=%s", "-1", sha)
        orig_msg = msg_out.strip() if rc2 == 0 else sha[:7]
        # Stage and auto-commit
        _run_git("add", "--", scope)
        rc3, out3, err3 = _run_git("commit", "-m", "Restored to: " + orig_msg)
        if rc3 != 0:
            # Might fail if nothing actually changed
            if "nothing to commit" in err3 or "nothing to commit" in out3:
                self.send_json(200, {"ok": True, "message": "Already at that version"})
                return
            self.send_json(500, {"error": err3.strip()})
            return
        self.send_json(200, {"ok": True, "message": "Restored to: " + orig_msg})

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
