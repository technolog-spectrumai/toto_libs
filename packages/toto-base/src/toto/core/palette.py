"""The one colour palette, read from a file (2026-10-10).

Every toto host wears the same colours: ``palette.json`` beside this module.
It used to be a database row (``ColorMix`` under a ``Theme``) that each host
seeded from its own copy of ``data/themes/amazing.json`` at every start, and
the stylesheet read the row's values from CSS variables written into each
page. The file is now the only source, and it is read when the image is
built: ``tailwind/tailwind.config.js`` compiles its colours into
``oya/tailwind.css``. A colour changes by a commit here and a rebuild of the
hosts, not in the admin.

What a page still needs at request time comes from the same file through
this module: the colour map the charts and graphs take as JSON
(``theme.theme.colors``), the Play CDN's inline configuration on a checkout
without the build, and the header's classes.

    {"name": ..., "font": "<Font.name>",
     "colors": {"<token>": "#rrggbb", ...},
     "header": {"light": "<classes>", "dark": "<classes>"}}

``font`` names the ``Font`` row a platform without a font is given
(``Platform.font``, by ``create_platform``, once: a font chosen in the admin
is kept); the row itself, with its stylesheet link, stays in the database.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

#: The file. Package data of toto-base (pyproject.toml), so a wheel has it.
PALETTE_FILE = Path(__file__).with_name("palette.json")

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PaletteError(ValueError):
    """palette.json is not a palette."""


def check(data) -> dict:
    """``data`` if it is a palette, else PaletteError naming what is wrong.
    A token is lower-case words joined by "-"; a colour is #rgb or #rrggbb
    (no alpha: Tailwind's opacity modifiers set their own)."""
    if not isinstance(data, dict):
        raise PaletteError("the palette is not an object")
    for key in ("name", "font"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise PaletteError(f"the palette has no {key!r}")
    colors = data.get("colors")
    if not isinstance(colors, dict) or not colors:
        raise PaletteError("the palette has no 'colors'")
    for token, value in colors.items():
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise PaletteError(f"{token!r} is not a colour token")
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise PaletteError(f"{token}: {value!r} is not a hex colour")
    header = data.get("header", {})
    if not isinstance(header, dict) or any(
            key not in ("light", "dark") or not isinstance(value, str)
            for key, value in header.items()):
        raise PaletteError("'header' is not {light, dark} class strings")
    return data


@lru_cache(maxsize=1)
def palette() -> dict:
    """The file, checked. Read once a process: it is part of the image."""
    try:
        data = json.loads(PALETTE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaletteError(f"{PALETTE_FILE}: {exc}") from exc
    return check(data)


def colors() -> dict:
    """``{"accent-light": "#2f3d63", ...}``, a copy the caller may keep."""
    return dict(palette()["colors"])


def header() -> dict:
    """The header's extra classes, ``{"light": ..., "dark": ...}``."""
    return {"light": "", "dark": "", **palette().get("header", {})}


def font_name() -> str:
    """The name of the ``Font`` row a new platform wears."""
    return palette()["font"]


def name() -> str:
    return palette()["name"]


def theme() -> dict:
    """The palette in the shape pages have always been handed as ``theme``
    (and as ``platform.theme``), without the font: ``theme.theme.colors``,
    ``platform.theme.header.light``."""
    return {"name": name(), "theme": {"colors": colors()}, "header": header()}
