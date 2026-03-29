#!/usr/bin/env python3
"""
assemble.py — Manuscript assembly and export.

Part of Bacalhau. Concatenates a hierarchical markdown directory into a single file.

Usage:
    python3 assemble.py <project-dir> --concat     # output assembled markdown
    python3 assemble.py <project-dir> --pdf         # generate .pdf (pure Python)
"""

import os
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


def cmd_pdf(output):
    """Generate .pdf (pure Python, no external deps)."""
    vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)
    from md2pdf import markdown_to_pdf
    text = concatenate()
    markdown_to_pdf(text, output)
    print(f"Generated {output}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    project_dir = None
    action = None
    output = None

    i = 0
    while i < len(args):
        if args[i] in ("--concat", "--pdf"):
            action = args[i][2:]
        elif args[i] == "-o" and i + 1 < len(args):
            output = args[i + 1]; i += 1
        elif not args[i].startswith("-"):
            project_dir = args[i]
        i += 1

    if not project_dir or not action:
        print("Usage: python3 assemble.py <project-dir> --concat|--pdf [-o output]")
        sys.exit(1)

    CHAPTERS_DIR = os.path.abspath(project_dir)

    if action == "concat":
        cmd_concat(output or "manuscript.md")
    elif action == "pdf":
        cmd_pdf(output or "manuscript.pdf")
