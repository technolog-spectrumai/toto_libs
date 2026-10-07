"""What the forum's pages ask that their views do not hand them (stage 70,
2026-10-07)."""

from __future__ import annotations

from django import template

from .. import access

register = template.Library()


@register.simple_tag
def forum_administrator(user) -> bool:
    """Is ``user`` a platform administrator, who may open the forum's
    Settings page? The page's link only: the Settings door asks for itself."""
    try:
        return bool(access.signed_in(user) and access.is_administrator(user))
    except Exception:  # noqa: BLE001 - a link is never worth a broken page
        return False
