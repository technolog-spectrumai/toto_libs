"""Tailwind built when the image is, or compiled in the browser (2026-10-01).

Every page used to load ``oya/tailwind.js``, Tailwind 3.4.17's Play CDN
script: 409 KB that compiled the page's classes in each visitor's browser,
with the colours handed to it in an inline ``tailwind.config``. A host that
builds ``oya/tailwind.css`` ahead of time (the Dockerfile's ``tailwind``
stage, ``scripts/tailwind_build.py``) serves that stylesheet instead.

The colours are compiled into it (2026-10-10): the build's configuration,
``toto/core/tailwind/tailwind.config.js``, reads the one palette,
``toto/core/palette.json``. Until then they were a database row, written
into every page as CSS variables (``--oya-<token>: R G B``) for the
stylesheet to read. Only the font is still a variable, ``--oya-font-sans``:
it is a row with a stylesheet link of its own (``Platform.font``).

A checkout without the build (a developer's runserver, the unit tests) has no
``oya/tailwind.css`` and keeps the Play CDN script, which is handed the same
palette inline, so it looks the same.
"""

from __future__ import annotations

from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders

register = template.Library()

#: The built stylesheet, where the host's build puts it.
BUILT_CSS = "oya/tailwind.css"


@lru_cache(maxsize=1)
def built_css_present() -> bool:
    """Whether the host built the stylesheet. Asked once a process: the
    file is part of the image, not of a request."""
    return bool(finders.find(BUILT_CSS))


@register.simple_tag
def tailwind_built() -> bool:
    return built_css_present()
