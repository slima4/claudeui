#!/usr/bin/env python3
"""
PostToolUse Hook — Reverse Dependency Check

After editing or writing a file, shows what other files import/depend
on it. Helps avoid accidentally breaking dependents.

Hook event: PostToolUse (matcher: Edit|Write)
Output: Shown to Claude as context after file modification.
"""

import json
import os
import re
import sys
from pathlib import Path

# Directories to skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", ".output", ".vercel",
    "target", "vendor", "out", "_build",
    ".idea", ".vscode", "coverage", ".pytest_cache",
}

MAX_DEPENDENTS = 10
MAX_SCAN_FILES = 2000


def get_file_basename(filepath):
    """Get importable name variations for a file."""
    p = Path(filepath)
    names = set()

    # Full relative path without extension
    stem = str(p.with_suffix(""))
    names.add(stem)
    names.add(p.name)                    # filename.ext
    names.add(p.stem)                    # filename without ext
    names.add(f"./{stem}")               # ./relative/path
    names.add(f"../{p.parent.name}/{p.stem}")  # ../dir/file

    # For index files, the directory name is the import target
    if p.stem == "index":
        names.add(str(p.parent))
        names.add(f"./{p.parent}")

    return names


def find_dependents(edited_file, project_root):
    """Find files that import/reference the edited file."""
    rel_path = edited_file
    try:
        rel_path = str(Path(edited_file).resolve().relative_to(Path(project_root).resolve()))
    except ValueError:
        pass

    search_names = get_file_basename(rel_path)
    dependents = []
    files_scanned = 0

    source_extensions = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".go", ".java", ".rs", ".c", ".cpp", ".h", ".hpp",
        ".vue", ".svelte", ".astro",
    }

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            if files_scanned >= MAX_SCAN_FILES:
                return dependents, True  # truncated

            ext = Path(fname).suffix.lower()
            if ext not in source_extensions:
                continue

            filepath = os.path.join(root, fname)

            # Don't check the file against itself
            try:
                if Path(filepath).resolve() == Path(edited_file).resolve():
                    continue
            except Exception:
                pass

            files_scanned += 1

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(50000)  # Cap read size
            except (FileNotFoundError, PermissionError):
                continue

            for name in search_names:
                # Look for import/require/from/use/include patterns containing this name
                if name in content:
                    try:
                        rel = str(Path(filepath).relative_to(project_root))
                    except ValueError:
                        rel = filepath
                    dependents.append(rel)
                    break

            if len(dependents) >= MAX_DEPENDENTS:
                return dependents, True  # truncated

    return dependents, False


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", tool_input.get("path", ""))
    if not file_path:
        return

    cwd = data.get("cwd", os.getcwd())

    dependents, truncated = find_dependents(file_path, cwd)

    if not dependents:
        return

    # Output for Claude's context
    lines = []
    count_str = f"{len(dependents)}+" if truncated else str(len(dependents))
    lines.append(f"⚠️ {count_str} file(s) depend on {Path(file_path).name}:")

    for dep in dependents:
        lines.append(f"  → {dep}")

    if truncated:
        lines.append("  ... (truncated)")

    lines.append("Consider checking these files for compatibility.")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
