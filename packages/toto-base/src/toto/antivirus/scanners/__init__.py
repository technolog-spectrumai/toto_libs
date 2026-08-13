"""The registry of things that can look at a file and object.

A scanner is a function ``(text: str) -> Verdict``. Registration is **pure
data**: this package is imported from ``AntivirusConfig.ready()``, which runs
before migrations and during ``collectstatic``, so nothing here may touch the
database or a setting that might not be loaded yet.

Every scanner **refuses; none rewrites.** That is not a security preference, it
is a statement about the file's contract: a rewritten file is one the author
never wrote, and for a document somebody is editing that is a worse outcome than
a refusal they can act on. The rule is delta's, from the SVG editor that needs
byte-exact round-tripping, and it generalises.

Adding a format is a module here plus one ``register()`` call. Another app adds
one by shipping ``<app>/scanners.py``.
"""

from __future__ import annotations

from toto.vault.scanning import Verdict

#: file_type -> callable(text) -> Verdict
_REGISTRY: dict[str, callable] = {}


class DuplicateScanner(ValueError):
    """Two scanners claimed one file type — one of them would never run."""


def register(file_type: str, scanner) -> None:
    """Claim a file type. Idempotent for the same callable, loud otherwise."""
    existing = _REGISTRY.get(file_type)
    if existing is not None and existing is not scanner:
        raise DuplicateScanner(
            f"{file_type!r} is already scanned by {existing!r}")
    _REGISTRY[file_type] = scanner


def scanner_for(file_type: str):
    return _REGISTRY.get(file_type)


def scanned_types() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


from . import json_scan, markup  # noqa: E402,F401  - the built-ins register on import
