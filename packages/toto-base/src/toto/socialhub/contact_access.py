"""Who sees a member's e-mail address and phone number (2026-10-01, 37c.25).

Every signed-in member saw every other member's e-mail address — on the
profile, in the roster and in the org-chart API — with no way to hide it, and
the phone number the same way. Each is now shown to other members only when
its owner switched it on (My account, Profile: ``Person.show_email`` and
``Person.show_phone``, both off by default). The member always sees their own;
an administrator — a real superuser on the Superuser plan, the standing rule
for superuser functions — keeps seeing both, as the admin pages always did.
What the data-mesh API hands out carries only what the member shows: its
caller's desktop client copies it on, peer to peer, to everybody else.

One rule for every door that shows another person's contact details, the way
`toto.locations.people_access` is for addresses, so the pages and the API
cannot drift apart.
"""

from __future__ import annotations

from django.apps import apps


def is_administrator(viewer) -> bool:
    """A real superuser and, on a host selling the Superuser plan, that plan
    in force (``toto.subscriptions.models.superuser_plan_active``)."""
    if not getattr(viewer, "is_authenticated", False):
        return False
    if not getattr(viewer, "is_superuser", False) or not getattr(viewer, "is_active", False):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.models import superuser_plan_active

    return superuser_plan_active(viewer)


def _own(viewer, person) -> bool:
    return (getattr(viewer, "is_authenticated", False)
            and person.user_id is not None and person.user_id == viewer.pk)


def may_see_email(viewer, person, *, passed_on=False) -> bool:
    """``passed_on=True`` for an answer its caller hands on to others — the
    data-mesh API's org chart, which the desktop client copies peer to peer:
    there the member's own choice is all that counts, so a hidden address
    leaves neither with an administrator nor in the member's own copy."""
    if person.show_email:
        return True
    return not passed_on and (_own(viewer, person) or is_administrator(viewer))


def may_see_phone(viewer, person, *, passed_on=False) -> bool:
    if person.show_phone:
        return True
    return not passed_on and (_own(viewer, person) or is_administrator(viewer))


def shown_email(viewer, person, **rule) -> str:
    """The address ``viewer`` may read on ``person``'s profile, or ""."""
    return (person.email or "") if may_see_email(viewer, person, **rule) else ""


def shown_phone(viewer, person, **rule) -> str:
    """The phone number ``viewer`` may read on ``person``'s profile, or ""."""
    return (person.phone or "") if may_see_phone(viewer, person, **rule) else ""
