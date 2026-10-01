"""Template-side view of who may see Storage's superuser tabs.

A tab that always answers 403 is worse than no tab (the base template says
it), so the tab strip asks what the tabs' doors ask. The
``remote_buckets_enabled`` tag that used to live here (the Remote tab's) is
gone (2026-10-01): Management's share and connect buttons read the page's
``share_connect_config`` (``vault_share.py``), the same switch as their
modals.
"""

from django import template

register = template.Library()


@register.filter
def superuser_plan(user) -> bool:
    """``{% if request.user|superuser_plan %}``: a superuser on the Superuser
    plan — the question every door of Storage's superuser tabs asks
    (``toto.vault.plan_gate``), so a tab never shows to someone its doors
    would refuse."""
    from toto.vault.plan_gate import superuser_plan_holder

    return superuser_plan_holder(user)
