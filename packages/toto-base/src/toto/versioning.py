"""Version coherence for the toto suite — build time, install time, runtime.

toto ships as several distributions that share the ``toto.*`` namespace and are
released **lockstep**: one repository tag ``vMAJOR.RELEASE`` produces every
package at exactly that version. A host declares what it needs in a manifest
(``requirements.toto.txt``) of exact pins, and three independent layers refuse
anything else:

1. **build time** — a host's ``deploy.py`` calls :func:`verify_checkout` before
   building wheels and :func:`verify_wheels` after, so a build against the wrong
   library checkout is rejected instead of shipped.
2. **install time** — each package pins its siblings exactly, so pip alone
   refuses a mixed suite.
3. **runtime** — :func:`check_runtime_coherence` runs from the core AppConfig
   and refuses to boot an incoherent installation.

This module is imported by deploy tooling *before* Django is configured, so it
must stay stdlib-only at import time.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SUITE_PREFIX = "toto-"
LEGACY_DIST = "toto"
SKIP_ENV = "TOTO_SKIP_VERSION_CHECK"

VERSION_RE = re.compile(r"^\d+\.\d+$")
PIN_RE = re.compile(r"^(toto-[a-z0-9-]+)==(\d+\.\d+)$")
WHEEL_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.]+?)-(?P<version>\d+\.\d+)(?:[-.].*)?\.whl$")


class TotoVersionError(RuntimeError):
    """A version mismatch, with a single actionable line telling you what to do."""

    def __init__(self, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def __str__(self) -> str:
        return f"{self.message}\n  fix: {self.remedy}" if self.remedy else self.message


@dataclass(frozen=True)
class Manifest:
    """The exact toto packages and version a host requires."""

    pins: dict[str, str]
    path: Path

    @property
    def version(self) -> str:
        return next(iter(self.pins.values()))

    @property
    def names(self) -> list[str]:
        return sorted(self.pins)

    def __len__(self) -> int:
        return len(self.pins)


def normalize(name: str) -> str:
    """PyPI name normalisation: wheel files spell ``toto-base`` as ``toto_base``."""
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


def read_manifest(path: str | os.PathLike[str]) -> Manifest:
    """Parse a host's ``requirements.toto.txt`` of exact toto pins.

    Deliberately strict: only ``toto-<name>==<major>.<release>`` lines are
    accepted. URLs are rejected so the manifest can never bind the suite to one
    git host, and every pin must name the same version (the lockstep rule).
    """
    path = Path(path)
    if not path.is_file():
        raise TotoVersionError(
            f"no toto manifest at {path}",
            "create it with one 'toto-<package>==<version>' line per required package",
        )

    pins: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN_RE.match(line)
        if not match:
            raise TotoVersionError(
                f"{path}:{lineno}: {line!r} is not an exact toto pin",
                "every line must read 'toto-<package>==<major>.<release>' — no URLs, no ranges",
            )
        pins[match.group(1)] = match.group(2)

    if not pins:
        raise TotoVersionError(f"{path} declares no toto packages", "add the packages this host needs")

    versions = set(pins.values())
    if len(versions) > 1:
        listed = ", ".join(f"{n}=={v}" for n, v in sorted(pins.items()))
        raise TotoVersionError(
            f"{path} mixes versions ({listed})",
            "the toto suite releases lockstep — pin every package to the same version",
        )
    return Manifest(pins=pins, path=path)


# --------------------------------------------------------------------------
# checkout
# --------------------------------------------------------------------------


def is_suite_checkout(src: str | os.PathLike[str]) -> bool:
    src = Path(src)
    return (src / "VERSION").is_file() and (src / "packages").is_dir()


def package_dir(src: str | os.PathLike[str], name: str) -> Path:
    """Directory of one package inside a toto_libs checkout."""
    path = Path(src) / "packages" / name
    if not (path / "pyproject.toml").is_file():
        raise TotoVersionError(
            f"{name} is not in the checkout at {src}",
            f"check out a tag that contains packages/{name}, or drop the pin from the manifest",
        )
    return path


def app_dir(src: str | os.PathLike[str], app: str) -> Path:
    """Find the source directory of a toto app without knowing which package owns it.

    Lets deploy tooling reach e.g. the forum app's templates without hardcoding
    that toto-chat happens to own them today.
    """
    matches = sorted(Path(src).glob(f"packages/*/src/toto/{app}"))
    if not matches:
        raise TotoVersionError(
            f"toto app {app!r} not found in the checkout at {src}",
            "check out a toto_libs revision that still ships this app",
        )
    return matches[0]


def checkout_version(src: str | os.PathLike[str]) -> str:
    version = (Path(src) / "VERSION").read_text().strip()
    if not VERSION_RE.match(version):
        raise TotoVersionError(
            f"{Path(src) / 'VERSION'} reads {version!r}",
            "the VERSION file must hold MAJOR.RELEASE, e.g. 1.4",
        )
    return version


def _git(src: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(src), *args],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def verify_checkout(src: str | os.PathLike[str], manifest: Manifest, *, strict: bool = True) -> str:
    """Check that a toto_libs checkout is exactly what the manifest asks for.

    Returns the version that will actually be built.

    ``strict=False`` is the local ``--dev`` build: the checkout still has to be
    a usable suite containing every pinned package, but it may be any version,
    on any branch, with uncommitted work. ``strict=True`` — every deploy — adds
    the version match plus "on the release tag, clean tree", which is what
    catches the everyday case of a sibling checkout drifting ahead of the pin
    while its VERSION file still reads the last release.
    """
    src = Path(src)
    wanted = manifest.version

    if not is_suite_checkout(src):
        legacy = "; this looks like a pre-split (single-package) checkout" if (src / "toto").is_dir() else ""
        raise TotoVersionError(
            f"{src} is not a toto suite checkout{legacy}",
            f"git -C {src} checkout v{wanted}",
        )

    found = checkout_version(src)
    for name in manifest.names:
        package_dir(src, name)

    if not strict:
        return found

    if found != wanted:
        raise TotoVersionError(
            f"toto_libs checkout {src} is at version {found}, this host requires {wanted}",
            f"git -C {src} checkout v{wanted}   (or pass --dev for a local, unpinned build)",
        )

    if not (src / ".git").exists():
        return wanted

    tags = _git(src, "tag", "--points-at", "HEAD")
    if tags is not None and f"v{wanted}" not in tags.split():
        described = (_git(src, "describe", "--always", "--dirty") or "").strip() or "unknown revision"
        raise TotoVersionError(
            f"toto_libs checkout {src} is not on tag v{wanted} (HEAD is {described})",
            f"git -C {src} checkout v{wanted}   (or pass --dev for a local, unpinned build)",
        )

    dirty = _git(src, "status", "--porcelain")
    if dirty:
        count = len(dirty.strip().splitlines())
        raise TotoVersionError(
            f"toto_libs checkout {src} has {count} uncommitted change(s) — the build would not match v{wanted}",
            f"commit or stash them, or pass --dev for a local, unpinned build",
        )
    return wanted


# --------------------------------------------------------------------------
# wheels
# --------------------------------------------------------------------------


def parse_wheel(path: str | os.PathLike[str]) -> tuple[str, str]:
    """(distribution name, version) from a wheel filename."""
    name = Path(path).name
    match = WHEEL_RE.match(name)
    if not match:
        raise TotoVersionError(f"cannot read a version from wheel filename {name!r}", "rebuild the wheels")
    return normalize(match.group("name")), match.group("version")


def verify_wheels(wheels: list[Path] | list[str], manifest: Manifest, *, exact_version: bool = True) -> None:
    """Check that the built wheels are exactly the manifest's packages and version.

    ``exact_version=False`` accompanies a ``--dev`` build: the set of packages
    must still be complete and free of leftovers, but their version is whatever
    the checkout produced. It also requires the wheels to agree with each other,
    so a half-rebuilt ``dist/`` cannot reach an image.
    """
    built: dict[str, str] = {}
    for wheel in wheels:
        name, version = parse_wheel(wheel)
        built[name] = version

    wanted = manifest.version
    if exact_version:
        wrong = {n: v for n, v in built.items() if n in manifest.pins and v != wanted}
        if wrong:
            listed = ", ".join(f"{n} {v}" for n, v in sorted(wrong.items()))
            raise TotoVersionError(
                f"built the wrong version: {listed} (this host requires {wanted})",
                f"check out tag v{wanted} in the toto_libs checkout and rebuild",
            )
    else:
        versions = {v for n, v in built.items() if n in manifest.pins}
        if len(versions) > 1:
            listed = ", ".join(f"{n} {v}" for n, v in sorted(built.items()))
            raise TotoVersionError(
                f"the staged wheels mix versions: {listed}",
                "clear dist/ and rebuild — every package must come from one checkout",
            )

    missing = sorted(set(manifest.pins) - set(built))
    if missing:
        raise TotoVersionError(
            f"missing wheels for {', '.join(missing)}",
            "rebuild: the manifest requires a wheel for every pinned package",
        )

    extra = sorted(set(built) - set(manifest.pins))
    if extra:
        raise TotoVersionError(
            f"unexpected toto wheels staged: {', '.join(extra)}",
            "clear the dist/ directory and rebuild, or add these packages to the manifest",
        )


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------


def installed_suite() -> dict[str, str]:
    """Installed toto-* distributions, by normalized name."""
    import importlib.metadata as md

    found: dict[str, str] = {}
    for dist in md.distributions():
        raw = dist.metadata["Name"]
        if not raw:
            continue
        name = normalize(raw)
        if name.startswith(SUITE_PREFIX) or name == LEGACY_DIST:
            found[name] = dist.version
    return found


def check_runtime_coherence() -> None:
    """Refuse to run on a mixed-version or half-upgraded toto installation.

    A no-op when nothing is installed (running straight from a checkout) or when
    ``TOTO_SKIP_VERSION_CHECK=1`` is set for local experiments.
    """
    if os.environ.get(SKIP_ENV) == "1":
        return

    installed = installed_suite()
    if LEGACY_DIST in installed:
        raise TotoVersionError(
            f"the pre-split 'toto' distribution {installed[LEGACY_DIST]} is still installed — "
            "it shadows the toto.* namespace packages",
            "pip uninstall -y toto   then reinstall the suite",
        )

    suite = {n: v for n, v in installed.items() if n.startswith(SUITE_PREFIX)}
    versions = set(suite.values())
    if len(versions) > 1:
        listed = ", ".join(f"{n} {v}" for n, v in sorted(suite.items()))
        raise TotoVersionError(
            f"the installed toto suite is incoherent: {listed}",
            "reinstall every package from one release: "
            "pip install --force-reinstall --no-index --find-links dist -r requirements.toto.txt",
        )
