#!/usr/bin/env python3
"""
assemble.py — Manuscript assembly and export.

Part of Bacalhau. Concatenates a hierarchical markdown directory into a single file.

Usage:
    python3 assemble.py <project-dir> --concat     # output assembled markdown
    python3 assemble.py <project-dir> --latex       # generate .tex via Pandoc
    python3 assemble.py <project-dir> --pdf         # generate .pdf via Pandoc
"""

import os
import shutil
import subprocess
import sys

# Set by main()
CHAPTERS_DIR = None


def read_order(directory):
    """Read _order.yaml from a directory. Returns list of entry names.
    Falls back to sorted directory listing if _order.yaml is missing."""
    order_file = os.path.join(directory, "_order.yaml")
    if os.path.exists(order_file):
        entries = []
        with open(order_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- "):
                    entry = line[2:].strip()
                    if entry:
                        entries.append(entry)
        # Append any files/dirs not in the order file
        on_disk = set()
        for name in os.listdir(directory):
            if name.startswith("_") or name.startswith("."):
                continue
            if os.path.isdir(os.path.join(directory, name)):
                on_disk.add(name + "/")
            elif name.endswith(".md"):
                on_disk.add(name)
        listed = set(entries)
        for extra in sorted(on_disk - listed):
            entries.append(extra)
        return entries
    else:
        # No order file — sorted listing
        entries = []
        for name in sorted(os.listdir(directory)):
            if name.startswith("_") or name.startswith("."):
                continue
            if os.path.isdir(os.path.join(directory, name)):
                entries.append(name + "/")
            elif name.endswith(".md"):
                entries.append(name)
        return entries


def write_order(directory, entries):
    """Write _order.yaml in a directory."""
    path = os.path.join(directory, "_order.yaml")
    with open(path, "w") as f:
        for entry in entries:
            f.write(f"- {entry}\n")


def walk_files(directory):
    """Recursively yield all .md file paths in order (depth-first)."""
    for entry in read_order(directory):
        if entry.endswith("/"):
            subdir = os.path.join(directory, entry.rstrip("/"))
            if os.path.isdir(subdir):
                yield from walk_files(subdir)
        else:
            path = os.path.join(directory, entry)
            if os.path.exists(path):
                yield path


import re as _re

_SCENE_HEADING = _re.compile(r"^(### )(.+)$", _re.MULTILINE)
_INTERMEZZO = _re.compile(r"^\*Intermezzo:")
_PART_HEADING = _re.compile(r"^## ")


def concatenate():
    """Concatenate all chapter files in tree order, injecting scene numbers."""
    parts = []
    scene_num = 0
    for filepath in walk_files(CHAPTERS_DIR):
        with open(filepath, "r") as f:
            content = f.read()
        basename = os.path.basename(filepath)
        # Only number ### headings in scene files (not _part.md, not intermezzos)
        if basename != "_part.md" and not basename.startswith("intermezzo-"):
            def inject_number(m):
                nonlocal scene_num
                title = m.group(2)
                # Skip if it's already numbered (shouldn't happen) or is a part/intermezzo
                if _INTERMEZZO.match(title) or _PART_HEADING.match(m.group(0)):
                    return m.group(0)
                scene_num += 1
                return f"{m.group(1)}{scene_num}. {title}"
            content = _SCENE_HEADING.sub(inject_number, content, count=1)
        parts.append(content)
    return "".join(parts)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_concat(output):
    """Assemble and write to output file."""
    text = concatenate()
    with open(output, "w") as f:
        f.write(text)
    files = list(walk_files(CHAPTERS_DIR))
    print(f"Assembled {len(files)} files → {output}")


def cmd_latex(output, template_dir=None):
    """Generate .tex via Pandoc."""
    if not shutil.which("pandoc"):
        print("Error: pandoc not found. Install: brew install pandoc", file=sys.stderr)
        sys.exit(1)
    text = concatenate()
    cmd = ["pandoc", "--from", "markdown", "--to", "latex", "-o", output]
    if template_dir:
        t = os.path.join(template_dir, "template.tex")
        m = os.path.join(template_dir, "metadata.yaml")
        if os.path.exists(t): cmd += ["--template", t]
        if os.path.exists(m): cmd += ["--metadata-file", m]
    proc = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"Pandoc error: {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Generated {output}")


def cmd_pdf(output, template_dir=None):
    """Generate .pdf via Pandoc + XeLaTeX."""
    if not shutil.which("pandoc"):
        print("Error: pandoc not found. Install: brew install pandoc", file=sys.stderr)
        sys.exit(1)
    if not shutil.which("xelatex"):
        print("Error: xelatex not found. Install: brew install --cask mactex-no-gui", file=sys.stderr)
        sys.exit(1)
    text = concatenate()
    cmd = ["pandoc", "--from", "markdown", "--to", "pdf", "--pdf-engine=xelatex", "-o", output]
    if template_dir:
        t = os.path.join(template_dir, "template.tex")
        m = os.path.join(template_dir, "metadata.yaml")
        if os.path.exists(t): cmd += ["--template", t]
        if os.path.exists(m): cmd += ["--metadata-file", m]
    proc = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"Pandoc error: {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Generated {output}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    global CHAPTERS_DIR

    args = sys.argv[1:]
    project_dir = None
    action = None
    output = None
    template_dir = None

    i = 0
    while i < len(args):
        if args[i] in ("--concat", "--latex", "--pdf"):
            action = args[i][2:]
        elif args[i] == "-o" and i + 1 < len(args):
            output = args[i + 1]; i += 1
        elif args[i] == "--templates" and i + 1 < len(args):
            template_dir = args[i + 1]; i += 1
        elif not args[i].startswith("-"):
            project_dir = args[i]
        i += 1

    if not project_dir or not action:
        print("Usage: python3 assemble.py <project-dir> --concat|-latex|--pdf [-o output] [--templates dir]")
        sys.exit(1)

    CHAPTERS_DIR = os.path.abspath(project_dir)

    if action == "concat":
        cmd_concat(output or "manuscript.md")
    elif action == "latex":
        cmd_latex(output or "manuscript.tex", template_dir)
    elif action == "pdf":
        cmd_pdf(output or "manuscript.pdf", template_dir)
