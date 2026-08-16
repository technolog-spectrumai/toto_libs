"""The vendored TipTap bundle, and the import map that points at it.

It lives in **cyprian** because cyprian is the only editor left in this wheel:
the presentation editor moved to the zinnia desktop app in 8/2026 and memo kept
only the read-only presenter, which needs none of this. The dependency that
used to run memo → cyprian now runs the other way.

The 54 vendored files are served from cyprian's static dir and named through an
import map, so the browser resolves bare specifiers to our own hashed URLs
rather than reaching the network.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from django.templatetags.static import static

VENDOR_DIR = Path(__file__).resolve().parent / "static" / "cyprian" / "vendor" / "tiptap"
MANIFEST = VENDOR_DIR / "manifest.json"
STATIC_PREFIX = "cyprian/vendor/tiptap/"

# Every `from "…"` in a vendored file whose target is a package rather than a
# relative path. The map has to answer all of them or the module 404s.
_BARE_IMPORT = re.compile(r"""(?:from|import)\s*["']([^"'./][^"']*)["']""")


@lru_cache(maxsize=1)
def manifest() -> dict[str, str]:
    """specifier -> vendored filename, written by scripts/fetch_tiptap.py."""
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A build without the vendored files still has to boot: the editor page
        # will fail loudly in the browser, the rest of the app will not.
        return {}


def import_map() -> dict:
    """The `{"imports": {...}}` object, with hashed static URLs."""
    return {"imports": {spec: static(STATIC_PREFIX + name)
                        for spec, name in sorted(manifest().items())}}


def import_map_json() -> str:
    """The import map as it goes into the page.

    `<` is escaped because this lands inside a `<script>` element, where a
    literal `</script>` in any value would end the element early. The values are
    our own static URLs, so this is belt and braces rather than a live risk.
    """
    return json.dumps(import_map(), separators=(",", ":")).replace("<", "\\u003c")


def bare_imports(text: str) -> set[str]:
    """Every package specifier a module imports. Used by the closure test."""
    return set(_BARE_IMPORT.findall(text))


def missing_specifiers() -> dict[str, set[str]]:
    """Specifiers imported by a vendored file that the map cannot resolve.

    Empty is the only acceptable answer: a gap here is a module that 404s at
    runtime, in the browser, after a deploy — which is exactly the failure an
    import map makes easy to introduce and easy to test for.
    """
    known = set(manifest())
    gaps: dict[str, set[str]] = {}
    for name in sorted(manifest().values()):
        path = VENDOR_DIR / name
        if not path.is_file():
            gaps[name] = {"<file missing>"}
            continue
        unresolved = bare_imports(path.read_text(encoding="utf-8")) - known
        if unresolved:
            gaps[name] = unresolved
    return gaps
