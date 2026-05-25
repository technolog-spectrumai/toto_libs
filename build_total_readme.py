#!/usr/bin/env python3
"""
Concatenate all app READMEs into a single file.

Usage:
    python build_total_readme.py [output_file]

Default output: total_readme.md

Each app is separated by a ================ block showing the app name.
Dependency sections are highlighted with a marker line.
The system overview (short_readme.md) is prepended at the top.
"""

import sys
import os

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm not found — install it: pip install tqdm", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_ROOT = os.path.join(SCRIPT_DIR, "toto")
SHORT_README = os.path.join(SCRIPT_DIR, "short_readme.md")

SEPARATOR = "=" * 72

DEPENDENCY_MARKER = "## Dependencies"


def find_app_readmes(apps_root: str) -> list[tuple[str, str]]:
    """Return sorted list of (app_name, readme_path) for all apps with a README."""
    results = []
    for entry in sorted(os.scandir(apps_root), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        readme = os.path.join(entry.path, "README.md")
        if os.path.isfile(readme):
            results.append((entry.name, readme))
    return results


def highlight_dependencies(content: str) -> str:
    """Wrap the ## Dependencies section with a visual marker."""
    if DEPENDENCY_MARKER not in content:
        return content
    lines = content.splitlines(keepends=True)
    output = []
    in_deps = False
    for line in lines:
        if line.strip() == DEPENDENCY_MARKER:
            in_deps = True
            output.append(f"{'─' * 48} DEPENDENCIES {'─' * 10}\n")
            output.append(line)
        elif in_deps and line.startswith("## "):
            # Next section — close the deps block
            output.append(f"{'─' * 72}\n")
            in_deps = False
            output.append(line)
        else:
            output.append(line)
    if in_deps:
        output.append(f"{'─' * 72}\n")
    return "".join(output)


def build(output_path: str) -> None:
    apps = find_app_readmes(APPS_ROOT)

    if not apps:
        print(f"No app READMEs found under {APPS_ROOT}", file=sys.stderr)
        sys.exit(1)

    sections: list[str] = []

    # --- System overview ---
    if os.path.isfile(SHORT_README):
        with open(SHORT_README) as f:
            overview = f.read().strip()
        sections.append(overview)
        sections.append(f"\n\n{SEPARATOR}\n{SEPARATOR}\n\n")

    # --- Per-app sections ---
    for app_name, readme_path in tqdm(apps, desc="Building total README", unit="app"):
        with open(readme_path) as f:
            content = f.read().strip()

        content = highlight_dependencies(content)

        header = f"{SEPARATOR}\n  APP: toto.{app_name}\n{SEPARATOR}"
        sections.append(f"{header}\n\n{content}\n")

    full_doc = "\n\n".join(sections)

    with open(output_path, "w") as f:
        f.write(full_doc)

    total_lines = full_doc.count("\n")
    total_kb = len(full_doc.encode()) // 1024
    print(f"\nWrote {output_path}  ({len(apps)} apps · {total_lines:,} lines · {total_kb} KB)")


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else "total_readme.md"
    build(output)
