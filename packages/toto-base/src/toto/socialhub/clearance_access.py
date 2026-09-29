"""Reading gated by clearances — the one rule, for every app that keeps things
to clearances (2026-09-29).

A ``Clearance`` names what it opens (``internal``, ``confidential``); its
members are whoever ``Person.clearances`` says. An app that gates an object
by clearances keeps a through table of ``(object, clearance)`` rows — reached
from the object by one related name, ``rows`` below, each row with a
``clearance`` FK — and asks here:

* **no clearance row → open**: the object is what it was before clearances
  (the app's own rule decides — public, owner, a directory ACL, "everyone
  signed in");
* **clearance rows → the members of any one of them**, the object's owner
  and superusers. Nobody else: not the public flag, not a folder's ACL —
  clearances win, so a thing kept to a clearance is kept;
* a user with no ``Person`` holds no clearance; anonymous visitors hold none.

**Hidden is missing.** An object a reader may not read answers as one that
does not exist; the app's doors 404 and its lists, counts and exports leave
it out. ``gate`` is the queryset half, ``hidden`` the per-object twin — the
same lookups, so the two cannot disagree. The wiki was first (zenobia's
``toto.wiki.access``); the vault, locations and places share this.

A community never grants reading: a clearance is its own model, so nothing
here can be handed one by mistake (README, "Communities and clearances").
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q


def person_of(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "community_profile", None)


def clearance_ids_of(user) -> set:
    """The clearances this user holds (pks); empty for no Person or anonymous."""
    person = person_of(user)
    if person is None:
        return set()
    return set(person.clearances.values_list("pk", flat=True))


def gate(user, queryset, *, rows: str, open: Q | None = None, owner: Q | None = None):
    """The objects in ``queryset`` that ``user`` may read.

    ``open`` is the app's own rule for an object with NO clearance (public, a
    claim, everyone — ``None`` means everyone); ``owner`` a Q naming the
    object's owner, who reads it whatever its clearances. An object WITH
    clearances is read by their members and its owner only — the app's rule
    no longer applies to it, in either direction: a clearance both keeps and
    grants.
    """
    if getattr(user, "is_superuser", False):
        return queryset
    no_clearance = Q(**{f"{rows}__isnull": True})
    rule = no_clearance & open if open is not None else no_clearance
    mine = clearance_ids_of(user)
    if mine:
        rule = rule | Q(**{f"{rows}__clearance__in": mine})
    if owner is not None and getattr(user, "is_authenticated", False):
        rule = rule | owner
    return queryset.filter(rule).distinct()


def kept(obj, *, rows: str) -> bool:
    """Whether ``obj`` is kept to clearances at all."""
    return getattr(obj, rows).exists()


def hidden(user, obj, *, rows: str, is_owner: bool = False) -> bool:
    """Whether clearances hide ``obj`` from ``user`` — the per-object twin of
    ``gate`` for an object that IS kept (``kept``); False for one that is
    not, where the app's own rule decides."""
    if obj is None:
        return True
    if getattr(user, "is_superuser", False) or is_owner:
        return False
    clearance_rows = getattr(obj, rows)
    if not clearance_rows.exists():
        return False
    mine = clearance_ids_of(user)
    return not (mine and clearance_rows.filter(clearance__in=mine).exists())


def clearances_of(obj, *, rows: str) -> list:
    """The object's clearances, by name ([] = open)."""
    from .models import Clearance

    return list(Clearance.objects.filter(pk__in=getattr(obj, rows).values("clearance_id"))
                .order_by("name"))


def shareable_clearances(user, obj, *, rows: str):
    """The clearances ``user`` may give ``obj`` to: every one for a superuser;
    otherwise those they hold plus the object's own — clearances are hidden
    from members, so an owner never sees one they do not hold."""
    from .models import Clearance

    clearances = Clearance.objects.order_by("name")
    if getattr(user, "is_superuser", False):
        return clearances
    return clearances.filter(Q(pk__in=clearance_ids_of(user))
                             | Q(pk__in=getattr(obj, rows).values("clearance_id"))).distinct()


def visible_clearances_of(user, obj, *, rows: str, manages: bool) -> list:
    """The object's clearances as ``user`` may see them: all of them for
    whoever manages the object, otherwise only those the viewer holds."""
    clearances = clearances_of(obj, rows=rows)
    if manages:
        return clearances
    mine = clearance_ids_of(user)
    return [c for c in clearances if c.pk in mine]


@transaction.atomic
def set_clearances(obj, clearances, *, rows: str, actor, action: str, app_label: str,
                   **facts) -> tuple[list, list]:
    """Make ``clearances`` the object's clearances; returns ``(before, after)``
    names. One audit record when something changed (``<APP_LABEL>.<ACTION>``:
    before, after, ``open`` when none is left).
    """
    clearances = list(clearances)
    current = getattr(obj, rows)
    before = sorted(current.values_list("clearance__name", flat=True))
    after = sorted({c.name for c in clearances})
    if before == after:
        return before, after
    wanted = {c.pk for c in clearances}
    current.exclude(clearance_id__in=wanted).delete()
    have = set(current.values_list("clearance_id", flat=True))
    for pk in wanted - have:
        current.create(clearance_id=pk)
    _audit(action, obj, actor, app_label=app_label, before=before, after=after,
           open=not after, **facts)
    return before, after


def _audit(action, obj, actor, *, app_label, **metadata):
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return
    from toto.audit.services import record

    try:
        with transaction.atomic():
            record(f"{app_label}.{action}", app_label=app_label, obj=obj, actor_user=actor,
                   metadata=metadata)
    except Exception:  # noqa: BLE001 - the chain never breaks an access change
        import logging

        logging.getLogger("toto.socialhub").exception("audit: could not record %s", action)
