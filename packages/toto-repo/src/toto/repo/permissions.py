"""Who may use git at all.

Version control is a staff tool on this platform: every toto.repo endpoint and
every surface toolbar sits behind ``REPO_ACCESS``. Three levels, the
ambrosia EXECUTION_ACCESS shape:

- ``"staff"`` (the default): staff and superusers.
- ``"superuser"``: superusers only.
- ``"authenticated"``: any signed-in user (the pre-gate behavior).

Unlike ambrosia's module-level constant, the setting is read lazily inside
the function — that is what lets ``override_settings`` exercise every level
in tests.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _


def access_level() -> str:
    return getattr(settings, "REPO_ACCESS", "staff")


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
        _("Version control is not enabled for your account "
          "(REPO_ACCESS is %(level)s).")
        % {"level": repr(access_level())}
    )
