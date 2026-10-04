"""Template-side view of who may see the Locations frame's superuser tab.

A tab that always answers 403 is worse than no tab (the socialhub's
``socialhub_flags`` and Storage's ``vault_flags`` say it too), so the strip
asks what the tab's doors ask.
"""

from django import template

register = template.Library()


@register.filter
def may_manage_domains(user) -> bool:
    """``{% if request.user|may_manage_domains %}``: a real superuser on the
    Superuser plan (2026-10-02) — the question every door of the Domains tab
    asks (``access.may_manage_domains``). The superuser bit alone showed the
    tab to a superuser off the plan."""
    from toto.locations.access import may_manage_domains as rule

    return rule(user)
