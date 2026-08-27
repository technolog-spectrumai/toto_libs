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
