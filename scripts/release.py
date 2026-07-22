#!/usr/bin/env python3
"""Set the lockstep version of every package in the toto suite.

The whole repository releases as one unit: a tag ``vMAJOR.RELEASE`` means every
distribution built from it carries exactly that version, and every sibling
dependency pins it exactly. This script is the only thing that writes those
numbers — ``VERSION``, each ``packages/*/pyproject.toml`` ``version``, and every
``toto-*==`` pin inside their ``dependencies``.

    python scripts/release.py 1.4        # rewrite
    python scripts/release.py --check    # verify coherence, write nothing

``scripts/check_package_graph.py`` and ``tests/test_versioning.py`` enforce the
same invariant, so an incoherent tree cannot pass the gates.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"
PACKAGES_DIR = REPO_ROOT / "packages"

VERSION_RE = re.compile(r"^\d+\.\d+$")
PROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*")[^"]*(")$', re.MULTILINE)
SIBLING_PIN_RE = re.compile(r'("toto-[a-z0-9-]+==)[^"]*(")')


def pyprojects() -> list[Path]:
    return sorted(PACKAGES_DIR.glob("*/pyproject.toml"))


def read_version() -> str:
    return VERSION_FILE.read_text().strip()


def apply(version: str) -> list[str]:
    changed: list[str] = []
    if read_version() != version:
        VERSION_FILE.write_text(version + "\n")
        changed.append("VERSION")
    for path in pyprojects():
        original = path.read_text()
        updated = PROJECT_VERSION_RE.sub(rf"\g<1>{version}\g<2>", original, count=1)
        updated = SIBLING_PIN_RE.sub(rf"\g<1>{version}\g<2>", updated)
        if updated != original:
            path.write_text(updated)
            changed.append(str(path.relative_to(REPO_ROOT)))
    return changed


def check(version: str) -> list[str]:
    problems: list[str] = []
    for path in pyprojects():
        text = path.read_text()
        match = PROJECT_VERSION_RE.search(text)
        rel = path.relative_to(REPO_ROOT)
        if not match:
            problems.append(f"{rel}: no [project] version")
        elif match.group(0).split('"')[1] != version:
            problems.append(f"{rel}: version {match.group(0).split('\"')[1]!r} != VERSION {version!r}")
        for pin in SIBLING_PIN_RE.finditer(text):
            pinned = text[pin.start():pin.end()].split("==")[1].strip('"')
            if pinned != version:
                problems.append(f"{rel}: sibling pin {pinned!r} != VERSION {version!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="new version, e.g. 1.4")
    parser.add_argument("--check", action="store_true", help="verify coherence only")
    args = parser.parse_args()

    if args.check or not args.version:
        version = read_version()
        problems = check(version)
        for problem in problems:
            print(f"ERROR: {problem}")
        if problems:
            print(f"FAILED: {len(problems)} version inconsistencies (fix: scripts/release.py {version})")
            return 1
        print(f"OK: all {len(pyprojects())} packages and their sibling pins are at {version}")
        return 0

    if not VERSION_RE.match(args.version):
        print(f"ERROR: {args.version!r} is not MAJOR.RELEASE (two integers, e.g. 1.4)")
        return 1

    changed = apply(args.version)
    for name in changed:
        print(f"    updated {name}")
    print(f"OK: suite is now {args.version} ({len(changed)} files changed)")
    print(f"    next: scripts/clean_env_check.sh && git commit -am 'release v{args.version}' && git tag v{args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
