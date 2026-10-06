"""Saving and removing the stored points and zones, under the charged-door
rule (``charging``).

A first save charges ``geography.pin`` (a point) or ``geography.zone`` (a
zone); a later change charges ``geography.note``; a save that changes
nothing charges nothing; removing is free. The rows, the usage event and the
charge are one transaction: a refused ledger leaves no point.

ONE AT A TIME FOR ONE OWNER. Every save and every removal opens its
transaction by taking its owner's row ``FOR UPDATE`` (the person, the
community: a row that is always there, where a link row may not be yet) and
reads what is saved only then. So what it decides from (is there a point
already? the first save's price, or a change's?) cannot change under it: of
two requests for one owner the second waits, then reads what the first one
left, and is a replay, an unchanged save or a charged change, as it would
have been a minute later.

WHAT THE WRITES REFUSE BY THEMSELVES. SQLite takes no row lock, and a rule
that holds only while everybody remembers the lock is no rule. So each write
also refuses to work from a stale read: a link's owner is unique, a row that
was read is updated and never made anew (``save()`` alone would put a
removed row back under its old id, with no link), and a new geometry row is
hung on a link only while that column is still empty. Any of the three
undoes the whole request (``_Raced``), and it is answered as the charged-door
rule says: a replay where its op was settled by this very request, 409
otherwise. That is what keeps one link per geometry row, always.

Nothing here writes ``Person.address`` or ``Community.seat``: the postal
text of a person and of a community stays where it is, in toto-base.

Each ``save_*`` answers ``(state, charged)``; ``state`` is what the door
sends back (``point_state``, ``zone_state``).
"""

from __future__ import annotations

from django.db import IntegrityError, connection, transaction
from django.utils.translation import gettext as _

from . import audit, charging, metrics, shapes
from .charging import Refusal
from .models import Address, CommunityHeadquarters, PersonAddress, Zone

NAME_MAX = 200
NOTE_MAX = 2000

POINT_LABEL = "Saved point"
ZONE_LABEL = "Saved zone"
CHANGE_LABEL = "Changed point or zone"


class _Raced(Exception):
    """What this request read is no longer so: another request wrote in
    between. Nothing of this one may be kept."""


def _text(value, limit, what) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Refusal(_("%(what)s must be text.") % {"what": what}, 400)
    if not charging.storable(value):
        raise Refusal(_("%(what)s holds a character that cannot be kept.") % {"what": what},
                      400)
    text = value.strip()
    if len(text) > limit:
        raise Refusal(_("%(what)s is too long: at most %(n)d characters.")
                      % {"what": what, "n": limit}, 400)
    return text


def point_state(address) -> dict | None:
    if address is None:
        return None
    lat, lng = shapes.pair_of(address.point)
    return {"lat": lat, "lng": lng, "name": address.name, "note": address.note}


def zone_state(zone) -> dict | None:
    if zone is None:
        return None
    return {"name": zone.name, "description": zone.description,
            "outline": shapes.corners_of(zone.outline)}


def _clean_point(lat, lng, name, note) -> dict:
    try:
        lat, lng = shapes.clean_pair(lat, lng)
    except shapes.BadShape as exc:
        raise Refusal(str(exc), 400) from None
    return {"lat": lat, "lng": lng,
            "name": _text(name, NAME_MAX, _("The name")),
            "note": _text(note, NOTE_MAX, _("The note"))}


# ---------------------------------------------------------------------------
# One at a time for one owner
# ---------------------------------------------------------------------------

def _hold(owner) -> bool:
    """Take ``owner``'s row until this transaction ends, so that the next
    request for the same owner waits here. False when the row is gone.

    ``FOR NO KEY UPDATE`` where the database has it: the same wait for the
    next save, without holding up a row that only points at this owner.
    SQLite takes no row lock and this is then a plain read."""
    rows = type(owner)._base_manager.select_for_update(
        no_key=connection.features.has_select_for_no_key_update)
    return bool(list(rows.filter(pk=owner.pk).values_list("pk", flat=True)))


def _one_at_a_time(user, owner, gone, op, body, attempt, stored):
    """``attempt()`` in one transaction that holds ``owner``'s row.

    Where a write found its read stale, everything of the request is undone
    first; then the op says what it was: settled by this very request, it is
    answered again with what is stored and charged nothing; anything else is
    409 (``charging.known`` raises it for another request under this op)."""
    try:
        with transaction.atomic():
            if not _hold(owner):
                raise Refusal(gone, 404)
            return attempt()
    except _Raced:
        if charging.known(user, op, body):
            return stored(), False
        raise Refusal(_("This was changed by another request at the same moment. "
                        "Press the button again."), 409) from None


def _new_link(make):
    """The link row of a first save. Its owner's column is unique, so the
    second of two first saves is refused here by the database."""
    try:
        with transaction.atomic():
            return make()
    except IntegrityError:
        raise _Raced() from None


def _still_there(row) -> None:
    """A row that was read is written only while it is still there."""
    if not type(row)._base_manager.filter(pk=row.pk).exists():
        raise _Raced()


def _hang(link, column, geometry) -> None:
    """Hang a new geometry row on its link, while that column is still
    empty: a second new row would leave the first with no link."""
    filled = (CommunityHeadquarters.objects
              .filter(pk=link.pk, **{f"{column}__isnull": True})
              .update(**{column: geometry}))
    if not filled:
        raise _Raced()
    setattr(link, column, geometry)


def _write_point(address, data, user) -> Address:
    point = shapes.point_of(data["lat"], data["lng"])
    if address is None:
        return Address.objects.create(point=point, name=data["name"], note=data["note"],
                                      created_by=user)
    _still_there(address)
    address.point, address.name, address.note = point, data["name"], data["note"]
    address.save(force_update=True)      # an update, never an insert
    return address


# ---------------------------------------------------------------------------
# A person's own point
# ---------------------------------------------------------------------------

def _person_link(person):
    return PersonAddress.objects.select_related("address").filter(person=person).first()


def save_person_point(user, person, *, lat, lng, name, note, op):
    data = _clean_point(lat, lng, name, note)
    op = charging.clean_op(op)
    body = {"person": person.pk, **data}

    def stored():
        link = _person_link(person)
        return point_state(link.address if link is not None else None)

    def attempt():
        link = _person_link(person)
        current = link.address if link is not None else None
        if charging.known(user, op, body) or point_state(current) == data:
            return point_state(current), False
        metric, label = ((metrics.NOTE, CHANGE_LABEL) if current is not None
                         else (metrics.PIN, POINT_LABEL))
        charging.afford(user, metric)

        def save():
            address = _write_point(current, data, user)
            if link is not None:
                return link
            return _new_link(lambda: PersonAddress.objects.create(person=person,
                                                                  address=address))

        try:
            saved, charged = charging.settle(user, metric, op, body, label, save=save)
        except charging.Duplicate:
            return stored(), False
        audit.record(audit.ADDRESS_SAVED, user, metric=metric,
                     amount=charging.amount_of(charged), outcome="charged", link=saved)
        return point_state(saved.address), True

    return _one_at_a_time(user, person, _("You have no profile yet."), op, body, attempt,
                          stored)


def clear_person_point(user, person) -> bool:
    """Remove the person's point: the ``Address`` row itself. Free."""
    with transaction.atomic():
        if not _hold(person):
            return False
        link = _person_link(person)
        if link is None:
            return False
        removed = (Address.objects.filter(pk=link.address_id).delete()[0]      # the link cascades
                   + PersonAddress.objects.filter(pk=link.pk).delete()[0])
        if not removed:
            return False
        audit.record(audit.ADDRESS_CLEARED, user, outcome="removed", link=link)
    return True


# ---------------------------------------------------------------------------
# A community's headquarters and zone
# ---------------------------------------------------------------------------

def _link_of(community):
    return (CommunityHeadquarters.objects.select_related("address", "zone")
            .filter(community=community).first())


def _drop_empty(community) -> None:
    """A link with neither a point nor a zone is no row."""
    CommunityHeadquarters.objects.filter(community=community, address__isnull=True,
                                         zone__isnull=True).delete()


def _own_link(community, link):
    """The community's link row for a save: the one that was read, or a new
    one where there was none."""
    if link is not None:
        return link
    return _new_link(lambda: CommunityHeadquarters.objects.create(community=community))


def save_headquarters(user, community, *, lat, lng, name, note, op):
    data = _clean_point(lat, lng, name, note)
    op = charging.clean_op(op)
    body = {"community": community.pk, "headquarters": data}

    def stored():
        link = _link_of(community)
        return point_state(link.address if link is not None else None)

    def attempt():
        link = _link_of(community)
        current = link.address if link is not None else None
        if charging.known(user, op, body) or point_state(current) == data:
            return point_state(current), False
        metric, label = ((metrics.NOTE, CHANGE_LABEL) if current is not None
                         else (metrics.PIN, POINT_LABEL))
        charging.afford(user, metric)

        def save():
            row = _own_link(community, link)
            address = _write_point(current, data, user)
            if current is None:
                _hang(row, "address", address)
            return row

        try:
            saved, charged = charging.settle(user, metric, op, body, label, save=save)
        except charging.Duplicate:
            return stored(), False
        audit.record(audit.HEADQUARTERS_SAVED, user, metric=metric, community=community,
                     amount=charging.amount_of(charged), outcome="charged", link=saved)
        return point_state(saved.address), True

    return _one_at_a_time(user, community, _("Community not found."), op, body, attempt,
                          stored)


def clear_headquarters(user, community) -> bool:
    with transaction.atomic():
        if not _hold(community):
            return False
        link = _link_of(community)
        if link is None or link.address_id is None:
            return False
        removed = Address.objects.filter(pk=link.address_id).delete()[0]     # SET_NULL on the link
        _drop_empty(community)
        if not removed:
            return False
        audit.record(audit.HEADQUARTERS_CLEARED, user, community=community,
                     outcome="removed", link=link)
    return True


def save_zone(user, community, *, name, description, outline, op):
    try:
        corners = shapes.clean_outline(outline)
        polygon = shapes.polygon_of(corners)
    except shapes.BadShape as exc:
        raise Refusal(str(exc), 400) from None
    data = {"name": _text(name, NAME_MAX, _("The name")),
            "description": _text(description, NOTE_MAX, _("The description")),
            "outline": [list(corner) for corner in corners]}
    op = charging.clean_op(op)
    body = {"community": community.pk, "zone": data}

    def stored():
        link = _link_of(community)
        return zone_state(link.zone if link is not None else None)

    def attempt():
        link = _link_of(community)
        current = link.zone if link is not None else None
        if charging.known(user, op, body) or zone_state(current) == data:
            return zone_state(current), False
        metric, label = ((metrics.NOTE, CHANGE_LABEL) if current is not None
                         else (metrics.ZONE, ZONE_LABEL))
        charging.afford(user, metric)

        def save():
            row = _own_link(community, link)
            if current is None:
                _hang(row, "zone", Zone.objects.create(
                    name=data["name"], description=data["description"], outline=polygon,
                    created_by=user))
                return row
            _still_there(current)
            current.name, current.description = data["name"], data["description"]
            current.outline = polygon
            current.save(force_update=True)      # an update, never an insert
            return row

        try:
            saved, charged = charging.settle(user, metric, op, body, label, save=save)
        except charging.Duplicate:
            return stored(), False
        audit.record(audit.ZONE_SAVED, user, metric=metric, community=community,
                     amount=charging.amount_of(charged), outcome="charged", link=saved)
        return zone_state(saved.zone), True

    return _one_at_a_time(user, community, _("Community not found."), op, body, attempt,
                          stored)


def clear_zone(user, community) -> bool:
    with transaction.atomic():
        if not _hold(community):
            return False
        link = _link_of(community)
        if link is None or link.zone_id is None:
            return False
        removed = Zone.objects.filter(pk=link.zone_id).delete()[0]
        _drop_empty(community)
        if not removed:
            return False
        audit.record(audit.ZONE_CLEARED, user, community=community,
                     outcome="removed", link=link)
    return True
