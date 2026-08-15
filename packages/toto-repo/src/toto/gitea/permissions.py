"""Who may use the hosted forge.

Same three levels and the same lazy read as ``toto.repo.permissions``, under its
own setting: the two apps run on different hosts, and a single knob would mean
turning one on to configure the other.

- ``"staff"`` (the default): staff and superusers.
- ``"superuser"``: superusers only.
- ``"authenticated"``: any signed-in user.

Note this gate is about the PORTAL's pages. Gitea enforces its own permissions
on everything behind them; a user who gets past this one still sees only the
repositories Gitea says are theirs.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _


def access_level() -> str:
    return getattr(settings, "GITEA_ACCESS", "staff")


def can_use(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    level = access_level()
    if level == "authenticated":
        return True
    if level == "superuser":
        return bool(user.is_superuser)
    return bool(user.is_staff or user.is_superuser)


def refusal() -> str:
    return (
        _("Code hosting is not enabled for your account "
          "(GITEA_ACCESS is %(level)s).")
        % {"level": repr(access_level())}
    )
