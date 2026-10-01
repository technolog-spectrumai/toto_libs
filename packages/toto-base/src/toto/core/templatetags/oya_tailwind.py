"""Tailwind built when the image is, or compiled in the browser (2026-10-01).

Every page used to load ``oya/tailwind.js``, Tailwind 3.4.17's Play CDN
script: 409 KB that compiled the page's classes in each visitor's browser,
with the theme's colours handed to it in an inline ``tailwind.config``. A
host that builds ``oya/tailwind.css`` ahead of time (zenobia: the
Dockerfile's ``tailwind`` stage, ``scripts/tailwind_build.py``) serves that
stylesheet instead. The theme is then not compiled in: its colours become
CSS variables, ``--oya-<token>: R G B``, which the build's colours
(``rgb(var(--oya-<token>) / <alpha-value>)``) read, so ``bg-accent-light/50``
works as it did; the theme font is ``--oya-font-sans``.

A checkout without the build (a developer's runserver, the unit tests) has no
``oya/tailwind.css`` and keeps the Play CDN script, so it looks the same.
"""

from __future__ import annotations

import re
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.utils.safestring import mark_safe

register = template.Library()

#: The built stylesheet, where the host's build puts it.
BUILT_CSS = "oya/tailwind.css"

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@lru_cache(maxsize=1)
def built_css_present() -> bool:
    """Whether the host built the stylesheet. Asked once a process: the
    file is part of the image, not of a request."""
    return bool(finders.find(BUILT_CSS))


@register.simple_tag
def tailwind_built() -> bool:
    return built_css_present()


def rgb_channels(value) -> str | None:
    """``#ff4081`` → ``255 64 129``; ``#f48`` too. An eight-digit colour
    loses its alpha (Tailwind's opacity modifiers set their own). Anything
    else is None."""
    if not isinstance(value, str) or not _HEX.fullmatch(value.strip()):
        return None
    digits = value.strip()[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return " ".join(str(int(digits[i:i + 2], 16)) for i in (0, 2, 4))


@register.simple_tag
def theme_color_vars(colors) -> str:
    """The theme's colours as ``--oya-<token>: R G B;`` declarations, for a
    ``:root`` rule. Only a token-shaped name and a hex value are written, so
    a theme row cannot put anything else into the page's stylesheet; a
    colour left out leaves its classes unstyled, as the Play CDN did."""
    if not isinstance(colors, dict):
        return ""
    out = []
    for name, value in colors.items():
        channels = rgb_channels(value)
        if channels and isinstance(name, str) and _TOKEN.fullmatch(name):
            out.append(f"--oya-{name}: {channels};")
    return mark_safe(" ".join(out))
