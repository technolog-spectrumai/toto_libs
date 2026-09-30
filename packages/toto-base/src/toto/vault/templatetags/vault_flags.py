"""Template-side view of the vault's host flags.

`VAULT_EXTERNAL_BUCKETS = False` closes every remote-bucket door — the
driver chokepoint, the refresh endpoints, bucket clean — but the Remote tab
used to be gated only on is_staff, so a hardened host showed operators a tab
whose every action answered 403 "disabled". The base template's own comment
says it: a tab that always answers 403 is worse than no tab.
"""

from django import template

from toto.vault.models import external_buckets_allowed

register = template.Library()


@register.simple_tag
def remote_buckets_enabled() -> bool:
    return external_buckets_allowed()


@register.filter
def superuser_plan(user) -> bool:
    """``{% if request.user|superuser_plan %}``: a superuser on the Superuser
    plan — the question every door of Storage's superuser tabs asks
    (``toto.vault.plan_gate``), so a tab never shows to someone its doors
    would refuse."""
    from toto.vault.plan_gate import superuser_plan_holder

    return superuser_plan_holder(user)
