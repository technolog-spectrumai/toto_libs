"""Reading gated by circles — the one rule, for every app that keeps things to
circles (2026-09-29).

A circle is a ``Community`` with ``is_circle``; its members are whoever
``Person.communities`` says. An app that gates an object by circles keeps a
through table of ``(object, circle)`` rows — reached from the object by one
related name, ``rows`` below, each row with a ``circle`` FK — and asks here:

* **no circle row → open**: the object is what it was before circles (the
  app's own rule decides — public, owner, a directory ACL, "everyone signed
  in");
* **circle rows → the members of any one of them**, the object's owner and
  superusers. Nobody else: not the public flag, not a folder's ACL — circles
  win, so a thing kept to a circle is kept;
* a user with no ``Person`` is in no circle; anonymous visitors are in none.

**Hidden is missing.** An object a reader may not read answers as one that
does not exist; the app's doors 404 and its lists, counts and exports leave
it out. ``narrow`` is the queryset half, ``hidden`` the per-object twin —
the same lookups, so the two cannot disagree. The wiki was first (zenobia's
``toto.wiki.access``); the vault, locations and places share this.

Functional communities and circles are orthogonal on purpose (README): a
functional community never grants reading, and ``set_circles`` refuses one.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _


class CircleRefused(ValueError):
    """A change to an object's circles the rules turn down."""


def person_of(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "community_profile", None)


def circle_ids_of(user) -> set:
    """The circles this user is in (pks); empty for no Person or anonymous."""
    person = person_of(user)
    if person is None:
        return set()
    return set(person.communities.filter(is_circle=True).values_list("pk", flat=True))


def gate(user, queryset, *, rows: str, open: Q | None = None, owner: Q | None = None):
    """The objects in ``queryset`` that ``user`` may read.

    ``open`` is the app's own rule for an object with NO circle (public, a
    claim, everyone — ``None`` means everyone); ``owner`` a Q naming the
    object's owner, who reads it whatever its circles. An object WITH
    circles is read by their members and its owner only — the app's rule no
    longer applies to it, in either direction: a circle both keeps and grants.
    """
    if getattr(user, "is_superuser", False):
        return queryset
    no_circle = Q(**{f"{rows}__isnull": True})
    rule = no_circle & open if open is not None else no_circle
    mine = circle_ids_of(user)
    if mine:
        rule = rule | Q(**{f"{rows}__circle__in": mine})
    if owner is not None and getattr(user, "is_authenticated", False):
        rule = rule | owner
    return queryset.filter(rule).distinct()


def kept(obj, *, rows: str) -> bool:
    """Whether ``obj`` is kept to circles at all."""
    return getattr(obj, rows).exists()


def hidden(user, obj, *, rows: str, is_owner: bool = False) -> bool:
    """Whether circles hide ``obj`` from ``user`` — the per-object twin of
    ``gate`` for an object that IS kept (``kept``); False for one that is
    not, where the app's own rule decides."""
    if obj is None:
        return True
    if getattr(user, "is_superuser", False) or is_owner:
        return False
    circle_rows = getattr(obj, rows)
    if not circle_rows.exists():
        return False
    mine = circle_ids_of(user)
    return not (mine and circle_rows.filter(circle__in=mine).exists())


def circles_of(obj, *, rows: str) -> list:
    """The object's circles, by name ([] = open)."""
    from .models import Community

    return list(Community.objects.filter(pk__in=getattr(obj, rows).values("circle_id"))
                .order_by("name"))


def shareable_circles(user, obj, *, rows: str):
    """The circles ``user`` may give ``obj`` to: every circle for a superuser;
    otherwise the circles they are in plus the object's own — circles are
    hidden from members, so an owner never sees one they are not in."""
    from .models import Community

    circles = Community.objects.circles().order_by("name")
    if getattr(user, "is_superuser", False):
        return circles
    return circles.filter(Q(pk__in=circle_ids_of(user))
                          | Q(pk__in=getattr(obj, rows).values("circle_id"))).distinct()


def visible_circles_of(user, obj, *, rows: str, manages: bool) -> list:
    """The object's circles as ``user`` may see them: all of them for whoever
    manages the object, otherwise only those the viewer is in."""
    circles = circles_of(obj, rows=rows)
    if manages:
        return circles
    mine = circle_ids_of(user)
    return [c for c in circles if c.pk in mine]


@transaction.atomic
def set_circles(obj, circles, *, rows: str, actor, action: str, app_label: str,
                **facts) -> tuple[list, list]:
    """Make ``circles`` the object's circles; returns ``(before, after)``
    names. Refuses anything that is not a circle. One audit record when
    something changed (``<APP_LABEL>.<ACTION>``: before, after, ``open`` when
    none is left).
    """
    circles = list(circles)
    if any(not c.is_circle for c in circles):
        raise CircleRefused(_("Only circles can be given something to read: a functional "
                              "community never grants reading."))
    current = getattr(obj, rows)
    before = sorted(current.values_list("circle__name", flat=True))
    after = sorted({c.name for c in circles})
    if before == after:
        return before, after
    wanted = {c.pk for c in circles}
    current.exclude(circle_id__in=wanted).delete()
    have = set(current.values_list("circle_id", flat=True))
    for pk in wanted - have:
        current.create(circle_id=pk)
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
