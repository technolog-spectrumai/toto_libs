"""The import map that makes a bundler-free TipTap possible.

It lives in **memo** because both editors in this wheel run on TipTap and only
one of them can own the 54 vendored files. cyprian already imports memo (its
sanitisers and media helpers) and memo must not import cyprian — a host can
install decks without documents — so the shared half sits at the bottom, here,
and each app keeps its own `tiptap_setup.js` with the extensions it actually
needs.

TipTap is ESM. Everything else vendored in this suite is a UMD bundle loaded
with a plain `<script src>`, because there is no bundler in this repo and adding
one is a far bigger change than the alternative — which is an **import map**:
the browser resolves the bare specifiers inside the vendored modules
(`import { Editor } from "@tiptap/core"`) to our own static URLs.

Four things had to be true for that to work here, and all four are:

* nginx emits `'unsafe-inline'` with no `script-src` override, so an inline
  `<script type="importmap">` is allowed (`scripts/deploy.py`);
* Django's `support_js_module_import_aggregation` is off, so bare specifiers
  pass through `collectstatic` untouched rather than being rewritten;
* unhashed originals are kept alongside the hashed ones, so nothing dangles;
* `toto.core.storage.ResilientManifestStaticFilesStorage` degrades instead of
  hard-failing on a reference it cannot resolve.

The map is built HERE rather than written into the template so that every
module goes through `static()` and is cache-busted with the rest of the site —
and so the closure test can prove the map covers every specifier the vendored
files actually import.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from django.templatetags.static import static

VENDOR_DIR = Path(__file__).resolve().parent / "static" / "memo" / "vendor" / "tiptap"
MANIFEST = VENDOR_DIR / "manifest.json"
STATIC_PREFIX = "memo/vendor/tiptap/"

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
