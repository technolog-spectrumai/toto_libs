"""Tools — the strip of things this platform DOES to a file.

This module is what survives of ``core/office.py``. Office — one hub over the
documents, presentations, sheets and drawings people MAKE — was retired to
limbo on 2026-09-02 (see zenobia/limbo/README.md): the sheets editor and the
HTML reader it aggregated were parked, and a hub over the two remaining file
types was exactly the "landing page as a hop to nowhere" that killed the FIRST
Office area in 0fc1f6ab→758bfa6c. The tools kept their room: they had already
moved out to ``/tools/`` on 2026-09-01, and reading a scan or rendering a PDF
never depended on Office listing anything.

## What this module may and may not do

**It imports none of the tool apps.** They are named as STRINGS for
`apps.is_installed`, and `url_name` is resolved lazily and dropped on
NoReverseMatch — `toto.aralia` is a zenobia HOST portion and this module ships
in the toto-base wheel, and a wheel may not import a host portion.

**Every route the hub owns is a GET.** `toto.subscriptions.gate` reads the
entitlement from `resolver_match.app_name`, and an app the catalogue does not
know is free — so a hub-owned write route would be a way to use paid tools for
nothing. Each tool's own app keeps its entitlement and its POSTs.
`tests_tools.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """One tool tab.

    A LINK, not a route — the hub owns no write routes and a tool needs some,
    so each tool's own app keeps its entitlement and its POSTs. `url_name` is
    a NAME, resolved lazily and dropped on NoReverseMatch, so an
    installed-but-unmounted app offers nothing rather than 500ing the hub.

    (A `Section` dataclass sat beside this until 2026-09-02 — an Office tab,
    defined by the vault file types it LISTS. Tools list nothing, which is why
    a tool has no rows, no folder panel, no New button and no sort.)
    """

    slug: str
    label: str
    icon: str
    url_name: str
    app_labels: tuple = ()
    blurb: str = ""
    entitlement: str = ""


TOOLS: tuple = (
    Tool(slug="ocr", label="Text recognition",
         icon="fa-solid fa-file-signature",
         url_name="ocr:home", app_labels=("toto.ocr",), entitlement="ocr",
         blurb="Turn a photo, a screenshot or a scanned PDF into text."),
    # Names a HOST-owned app, which this module may do: `app_labels` is a
    # string for `apps.is_installed` and `url_name` is resolved lazily, so
    # nothing here imports it.
    Tool(slug="aralia", label="HTML to PDF", icon="fa-solid fa-file-pdf",
         url_name="aralia:editor", app_labels=("toto.aralia",),
         entitlement="aralia",
         blurb="Preview an HTML document, and render it to a PDF."),
)


def available_tools() -> list:
    """The tools this host both installs and mounts.

    Kept as its own function even though `tools_tabs()` is what the page
    renders: a caller asking "does this host read scans?" should not have to
    filter a list of tabs to find out.
    """
    from django.apps import apps as django_apps
    from django.urls import NoReverseMatch, reverse

    out = []
    for tool in TOOLS:
        if tool.app_labels and not any(django_apps.is_installed(label)
                                       for label in tool.app_labels):
            continue
        try:
            url = reverse(tool.url_name)
        except NoReverseMatch:
            continue
        out.append({"slug": tool.slug, "label": tool.label, "icon": tool.icon,
                    "url": url, "blurb": tool.blurb})
    return out


def tools_tabs(active: str = "") -> list:
    """THE tools strip.

    The tool pages live in different apps (`/ocr/`, `/aralia/`), so without a
    shared strip, arriving at one drops you out of the set you picked it from.
    So the hub and every tool page render THIS, and one builder produces it —
    a tool cannot appear on the hub and be missing from its own page.

    `active` is a SLUG, not a URL: the page is in another app entirely, so
    there is no request path to compare against and only the page knows which
    tab it is.
    """
    return [{**tool, "active": tool["slug"] == active}
            for tool in available_tools()]
