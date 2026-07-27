"""Shared access to the built wheels.

The packaging tests run against real wheels, not the source tree. Point them at
a directory with ``TOTO_WHEEL_DIR``; ``scripts/clean_env_check.sh`` does that
after building every package from its sdist.
"""
import os
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = sorted(p.name for p in (REPO_ROOT / "packages").iterdir() if p.is_dir())


def wheel_dir() -> Path:
    return Path(os.environ.get("TOTO_WHEEL_DIR", REPO_ROOT / "dist"))


def wheel_for(package: str) -> Path | None:
    """Newest wheel of one distribution (wheel filenames use underscores)."""
    matches = sorted(
        wheel_dir().glob(f"{package.replace('-', '_')}-*.whl"),
        key=lambda p: p.stat().st_mtime,
    )
    return matches[-1] if matches else None


@pytest.fixture(scope="session")
def wheels() -> dict[str, Path]:
    built = {name: wheel_for(name) for name in PACKAGES}
    missing = sorted(name for name, path in built.items() if path is None)
    if missing:
        pytest.skip(f"no wheels for {', '.join(missing)} in {wheel_dir()} — run scripts/build_wheels.py")
    return {name: path for name, path in built.items() if path}


@pytest.fixture(scope="session")
def payloads(wheels: dict[str, Path]) -> dict[str, list[str]]:
    """Package name -> its wheel entries, excluding .dist-info metadata."""
    out: dict[str, list[str]] = {}
    for name, path in wheels.items():
        with zipfile.ZipFile(path) as zf:
            out[name] = [n for n in zf.namelist() if ".dist-info/" not in n]
    return out


@pytest.fixture(scope="session")
def all_names(payloads: dict[str, list[str]]) -> set[str]:
    return {name for entries in payloads.values() for name in entries}


@pytest.fixture(scope="session")
def owner(payloads: dict[str, list[str]]) -> dict[str, str]:
    """Wheel entry -> the package that ships it."""
    return {entry: pkg for pkg, entries in payloads.items() for entry in entries}
