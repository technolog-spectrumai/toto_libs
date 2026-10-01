"""Template-side view of who may see the socialhub's superuser tab.

A tab that always answers 403 is worse than no tab (Storage's strip says it
too, ``vault_flags``), so the strip asks what the tab's doors ask.
"""

from django import template

register = template.Library()


@register.filter
def may_manage_clearances(user) -> bool:
    """``{% if user|may_manage_clearances %}``: a real superuser on the
    Superuser plan (2026-10-01, 37c.32) — the question every door of the
    Clearances tab asks (``views.clearances.may_manage``). The superuser bit
    alone showed the tab to a superuser off the plan, whose every click on it
    was a 403 page."""
    from toto.socialhub.views.clearances import may_manage

    return may_manage(user)
