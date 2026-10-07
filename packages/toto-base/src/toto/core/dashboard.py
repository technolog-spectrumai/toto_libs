"""Sections of the dashboard that an app fills for the member looking at it.

The dashboard's tiles are settings: ``DASHBOARD_ITEMS`` grouped by
``DASHBOARD_CATEGORIES``, the same for everybody whom a tile's visibility and
plan let through. A tile cannot say "one entry for each of YOUR
communities". This is the extension point for that, and it is small on
purpose (2026-10-07; its first user is the forum's section):

    # <app>/plugins/dashboard_sections.py
    @DashboardSection.plugin(key="forum", title=_("Forum"), order=20)
    class ForumSection(DashboardSection):
        def section(self, request):
            return {"items": [{"title", "description", "icon", "link"}, …],
                    "note": "", "note_link": "", "note_label": ""}

``section(request)`` answers None for a member the section is not for (it
is then not drawn at all), or the entries, drawn as the tiles are
(``oya/_dashboard_cards.html``: a title, a description, an icon, a link, all
written as text), and optionally one sentence with a link under them, for a
section with nothing to list. WHO sees WHAT is the plugin's own rule: this
module asks nobody. A section is drawn for a signed-in member only, after
the first group of tiles, and its entries are not counted among the
dashboard's modules.

Plugins are found by ``autodiscover_plugins("plugins.dashboard_sections")``
the first time a dashboard is drawn. A plugin that raises is logged and left
out: a dashboard must render whatever breaks.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from .plugin import BasePlugin

logger = logging.getLogger(__name__)


class DashboardSection(BasePlugin):
    """One section of the dashboard, filled per member (module docstring)."""

    registry: ClassVar[dict[str, "DashboardSection"]] = {}

    def section(self, request) -> dict[str, Any] | None:
        return None


_discovered = False


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    from .plugin_autodiscover import autodiscover_plugins

    autodiscover_plugins("plugins.dashboard_sections")
    _discovered = True


def sections_for(request) -> list[dict[str, Any]]:
    """The sections drawn for this request's member, in the plugins' order:
    ``[{"key", "title", "items", "note", "note_link", "note_label"}]``."""
    _discover()
    out = []
    for plugin in DashboardSection.all():
        try:
            made = plugin.section(request)
        except Exception:  # noqa: BLE001 - a dashboard must render whatever breaks
            logger.exception("dashboard section %r failed; left out", plugin.get_key())
            continue
        if not made:
            continue
        out.append({
            "key": plugin.get_key(),
            "title": made.get("title") or plugin.get_title(),
            "items": list(made.get("items") or []),
            "note": made.get("note") or "",
            "note_link": made.get("note_link") or "",
            "note_label": made.get("note_label") or "",
        })
    return out
