"""A community's organisation chart (2026-10-06, stage 66): positions, the
people assigned to them and who reports to whom.

Every community has one, a company as every other kind; it starts empty.
``CommunityPosition`` is the row; this module is the only writer the pages
use, and holds the chart's rules:

* a position reports to a position of the SAME community, or to none;
* never to itself, and never to one that reports, however far up, to it:
  ``would_ring`` walks up from the proposed superior and refuses when it
  meets the position (the walk the old company register's departments
  used);
* every change first locks the community's row, so two changes at once are
  made one after the other: each alone would pass the walk, and together
  they would close a ring;
* deleting a position hands those that reported to it to what it reported
  to, so nobody is left hanging from a hole.

Who may change a chart is ``permissions.may_moderate_community`` (the head,
an administrator), asked by the doors (``views/org_chart.py``). Nothing here
reads a share register: a person's position is what a manager set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils.translation import gettext as _

#: The longest title kept, and the largest order number.
TITLE_MAX = 120
ORDER_MAX = 9999
#: The tab's name in the community page's address.
TAB = "chart"


class ChartError(ValueError):
    """What was asked cannot be: the sentence is for the person asking."""


@dataclass
class Row:
    """One position as the page draws it: how deep it hangs, and which
    positions it may be moved under (everything but itself and what hangs
    below it)."""

    position: object
    depth: int = 0
    may_report_to: list = field(default_factory=list)

    @property
    def indent(self):
        return range(self.depth)


def _lock(community):
    """The community's row, locked until the transaction ends."""
    from .models import Community

    return Community.objects.select_for_update().get(pk=community.pk)


def clean_title(text) -> str:
    title = " ".join(str(text or "").split())
    if not title:
        raise ChartError(_("Give the position a name."))
    if len(title) > TITLE_MAX:
        raise ChartError(_("The position's name is too long: at most %(max)d characters.")
                         % {"max": TITLE_MAX})
    if not all(character.isprintable() for character in title):
        raise ChartError(_("The position's name is one line of text."))
    return title


def clean_order(text) -> int:
    text = str(text if text is not None else "").strip()
    if not text:
        return 0
    if not (text.isascii() and text.isdigit()) or int(text) > ORDER_MAX:
        raise ChartError(_("The order is a whole number from 0 to %(max)d.")
                         % {"max": ORDER_MAX})
    return int(text)


def would_ring(position_pk, superior) -> bool:
    """Would ``position_pk`` reporting to ``superior`` close a ring? True
    when ``superior`` is the position itself or reports, at any distance, to
    it. The walk also stops at a position met twice, so a ring already in
    the table (there should be none) cannot make it run for ever."""
    seen = set()
    current = superior
    while current is not None:
        if current.pk == position_pk:
            return True
        if current.pk in seen:
            return False
        seen.add(current.pk)
        current = current.reports_to
    return False


def _superior(community, reports_to_pk, position_pk=None):
    """The position ``reports_to_pk`` names in ``community``, or None for
    none; refused when it is another community's, missing, or would close a
    ring."""
    from .models import CommunityPosition

    if reports_to_pk in (None, "", 0, "0"):
        return None
    try:
        wanted = int(reports_to_pk)
    except (TypeError, ValueError):
        raise ChartError(_("Choose a position of this community to report to.")) from None
    superior = CommunityPosition.objects.filter(pk=wanted, community=community).first()
    if superior is None:
        raise ChartError(_("Choose a position of this community to report to."))
    if position_pk is not None and would_ring(position_pk, superior):
        raise ChartError(_("A position cannot report to itself or to one that reports to it."))
    return superior


def create(community, *, title, person=None, reports_to=None, order=0):
    """A new position. ``reports_to`` is a position's id, or nothing."""
    from .models import CommunityPosition

    title, order = clean_title(title), clean_order(order)
    with transaction.atomic():
        _lock(community)
        superior = _superior(community, reports_to)
        return CommunityPosition.objects.create(
            community=community, title=title, person=person, reports_to=superior,
            order=order)


def change(community, position_pk, *, title, person=None, reports_to=None, order=0):
    """Change a position of ``community``: its name, who is assigned, what
    it reports to, its order. None when the community has no such position."""
    from .models import CommunityPosition

    title, order = clean_title(title), clean_order(order)
    with transaction.atomic():
        _lock(community)
        position = CommunityPosition.objects.filter(pk=position_pk,
                                                    community=community).first()
        if position is None:
            return None
        superior = _superior(community, reports_to, position.pk)
        position.title, position.person = title, person
        position.reports_to, position.order = superior, order
        position.save()
        return position


def delete(community, position_pk) -> bool:
    """Delete a position of ``community``; what reported to it now reports
    to what it reported to. False when the community has no such position."""
    from .models import CommunityPosition

    with transaction.atomic():
        _lock(community)
        position = CommunityPosition.objects.filter(pk=position_pk,
                                                    community=community).first()
        if position is None:
            return False
        for report in CommunityPosition.objects.filter(reports_to=position):
            report.reports_to_id = position.reports_to_id
            report.save()
        position.delete()
        return True


def positions_of(community) -> list:
    from .models import CommunityPosition

    return list(CommunityPosition.objects.filter(community=community)
                .select_related("person").order_by("order", "title", "pk"))


def chart_of(community) -> list[Row]:
    """The chart as rows, each top position followed by what hangs below
    it. A position whose superior is not in the chart, or that sits in a
    ring, is drawn at the top rather than lost."""
    positions = positions_of(community)
    by_pk = {position.pk: position for position in positions}
    below: dict = {}
    for position in positions:
        below.setdefault(position.reports_to_id if position.reports_to_id in by_pk else None,
                         []).append(position)
    rows, placed = [], set()

    def hang(position, depth):
        if position.pk in placed:
            return
        placed.add(position.pk)
        rows.append(Row(position, depth))
        for report in below.get(position.pk, []):
            hang(report, depth + 1)

    for top in below.get(None, []):
        hang(top, 0)
    for position in positions:          # anything a ring kept from the top
        hang(position, 0)

    def under(pk, found):
        for report in below.get(pk, []):
            if report.pk not in found:
                found.add(report.pk)
                under(report.pk, found)
        return found

    for row in rows:
        barred = under(row.position.pk, {row.position.pk})
        row.may_report_to = [position for position in positions
                             if position.pk not in barred]
    return rows


def nodes_of(community) -> list[dict]:
    """The chart for the Org Chart drawing (``org_chart_scripts.html``): a
    node per position, with the holder's name or "Vacant", the position's
    name, and the node it hangs from."""
    from django.urls import reverse

    positions = positions_of(community)
    known = {position.pk for position in positions}
    nodes = []
    for position in positions:
        person = position.person
        nodes.append({
            "id": f"pos-{position.pk}",
            "name": person.display_name if person is not None else _("Vacant"),
            "title": position.title,
            "pid": (f"pos-{position.reports_to_id}"
                    if position.reports_to_id in known else None),
            "profile_url": (reverse("socialhub:profile_details", args=[person.slug])
                            if person is not None else ""),
        })
    return nodes
