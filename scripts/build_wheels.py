#!/usr/bin/env python3
"""Build wheels for the toto suite — no package index, any git host.

    python scripts/build_wheels.py                       # all packages -> dist/
    python scripts/build_wheels.py --only toto-base,toto-flow
    python scripts/build_wheels.py --sdist               # sdist, then wheel FROM it

The printed install line is the one to use on the target machine: it installs
from the built wheels alone (``--no-index``), so the exact sibling pins are
resolved against these files and nothing is fetched from the network.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Gitignored asset trees that a wheel must never carry, relative to a package
#: directory. `download_vendor.py` fetches these at IMAGE build time, straight
#: into the installed package under site-packages; a developer who points it at
#: a source checkout instead leaves 2.3 MB of fontawesome, htmx and alpine
#: inside packages/toto-base/src, and every wheel built afterwards ships them.
#:
#: pyproject's `exclude-package-data` was written to prevent exactly this and
#: CANNOT: `packages.find` runs with `namespaces = true`, so setuptools
#: discovers `toto.core.static.vendor.fontawesome` and its siblings as packages
#: in their own right, and an exclusion keyed on `toto.core` never sees files
#: that belong to a different package. Every pattern shape was tried; the four
#: files directly under vendor/ drop out and the nine in subdirectories do not.
#:
#: So it is pruned here, the same way and for the same reason as `build/` below:
#: both are gitignored, both are regenerable, and both otherwise produce a
#: silently wrong wheel on one machine and a correct one on another.
PRUNE_BEFORE_BUILD = ("src/toto/core/static/vendor",)


def prune_downloaded_assets(pkg: Path) -> None:
    """Remove gitignored downloaded assets from a package before building it."""
    for relative in PRUNE_BEFORE_BUILD:
        tree = pkg / relative
        if tree.is_dir():
            print(f"    pruning downloaded assets: {tree.relative_to(REPO_ROOT)}")
            shutil.rmtree(tree)


def package_dirs(only: str | None) -> list[Path]:
    available = {p.name: p for p in sorted((REPO_ROOT / "packages").iterdir()) if p.is_dir()}
    if not only:
        return list(available.values())
    chosen: list[Path] = []
    for name in (n.strip() for n in only.split(",") if n.strip()):
        if name not in available:
            sys.exit(f"ERROR: unknown package {name!r}; available: {', '.join(available)}")
        chosen.append(available[name])
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated package names")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--sdist", action="store_true", help="build via sdist (proves MANIFEST.in)")
    parser.add_argument("--keep", action="store_true", help="keep existing files in the output dir")
    args = parser.parse_args()

    version = (REPO_ROOT / "VERSION").read_text().strip()
    packages = package_dirs(args.only)
    out: Path = args.out
    if out.exists() and not args.keep:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for pkg in packages:
        # setuptools reuses packages/<pkg>/build/lib and copies whatever is in
        # it into the wheel, so a file deleted from src/ keeps shipping until
        # that tree is cleared. build/ is gitignored, so this only ever bites on
        # a machine that built before the deletion — which is exactly the case
        # that must not produce a silently wrong wheel.
        stale_build = pkg / "build"
        if stale_build.is_dir():
            shutil.rmtree(stale_build)
        prune_downloaded_assets(pkg)

        if args.sdist:
            subprocess.run([sys.executable, "-m", "build", "--sdist", "--outdir", str(out), str(pkg)], check=True)
            sdist = max(out.glob(f"{pkg.name.replace('-', '_')}-*.tar.gz"), key=lambda p: p.stat().st_mtime)
            target: str = str(sdist)
        else:
            target = str(pkg)
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(out), target],
            check=True,
        )

    wheels = sorted(out.glob("*.whl"))
    print(f"\nbuilt {len(wheels)} wheels in {out}:")
    for wheel in wheels:
        print(f"    {wheel.name}")
    pins = " ".join(f"{pkg.name}=={version}" for pkg in packages)
    print(f"\ninstall with:\n    pip install --no-index --find-links {out} {pins}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
