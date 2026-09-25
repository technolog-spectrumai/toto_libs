"""Who may change a location object.

Everybody signed in reads the map. Writing is narrower (2026-09-25): an
address or a route may have its metadata and note changed by whoever created
it, or by staff; a row from before `created_by` existed has no creator and is
staff's alone. Territories, zones and route chains arrive from imported
layers and the admin, so only staff change them. Importing a map layer is a
staff act — it creates shared rows everybody sees.
"""

from __future__ import annotations


def is_staff(user) -> bool:
    return bool(getattr(user, "is_authenticated", False)
                and (user.is_staff or user.is_superuser))


def may_write(user, obj) -> bool:
    if is_staff(user):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    creator = getattr(obj, "created_by_id", None)
    return creator is not None and creator == user.pk


def may_import_layer(user) -> bool:
    return is_staff(user)
