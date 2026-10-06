"""Saving and removing the stored points and zones, under the charged-door
rule (``charging``).

A first save charges ``geography.pin`` (a point) or ``geography.zone`` (a
zone); a later change charges ``geography.note``; a save that changes
nothing charges nothing; removing is free. The rows, the usage event and the
charge are one transaction: a refused ledger leaves no point.

Nothing here writes ``Person.address`` or ``Community.seat``: the postal
text of a person and of a community stays where it is, in toto-base.

Each ``save_*`` answers ``(state, charged)``; ``state`` is what the door
sends back (``point_state``, ``zone_state``).
"""

from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext as _

from . import audit, charging, metrics, shapes
from .charging import Refusal
from .models import Address, CommunityHeadquarters, PersonAddress, Zone

NAME_MAX = 200
NOTE_MAX = 2000

POINT_LABEL = "Saved point"
ZONE_LABEL = "Saved zone"
CHANGE_LABEL = "Changed point or zone"


def _text(value, limit, what) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Refusal(_("%(what)s must be text.") % {"what": what}, 400)
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


def _write_point(address, data, user) -> Address:
    if address is None:
        address = Address(created_by=user)
    address.point = shapes.point_of(data["lat"], data["lng"])
    address.name, address.note = data["name"], data["note"]
    address.save()
    return address


def _settle(user, metric, op, body, label, save, current):
    """``settle`` for a save: ``(link row, the ledger's answer, settled)``.
    A concurrent duplicate answers what the other request saved, unsettled
    here and so uncharged."""
    try:
        saved, charged = charging.settle(user, metric, op, body, label, save=save)
    except charging.Duplicate:
        return current(), None, False
    return saved, charged, True


# ---------------------------------------------------------------------------
# A person's own point
# ---------------------------------------------------------------------------

def save_person_point(user, person, *, lat, lng, name, note, op):
    data = _clean_point(lat, lng, name, note)
    op = charging.clean_op(op)
    body = {"person": person.pk, **data}
    link = PersonAddress.objects.select_related("address").filter(person=person).first()
    current = link.address if link is not None else None
    if charging.known(user, op, body) or point_state(current) == data:
        return point_state(current), False
    metric = metrics.NOTE if current is not None else metrics.PIN
    charging.afford(user, metric)

    def save():
        address = _write_point(current, data, user)
        if link is None:
            return PersonAddress.objects.create(person=person, address=address)
        return link

    def again():
        return PersonAddress.objects.filter(person=person).first()

    saved, charged, settled = _settle(
        user, metric, op, body, CHANGE_LABEL if current is not None else POINT_LABEL,
        save, again)
    if not settled:
        return point_state(saved.address if saved is not None else None), False
    audit.record(audit.ADDRESS_SAVED, user, metric=metric,
                 amount=charging.amount_of(charged), outcome="charged", link=saved)
    return point_state(saved.address if saved is not None else None), True


def clear_person_point(user, person) -> bool:
    """Remove the person's point: the ``Address`` row itself. Free."""
    link = PersonAddress.objects.filter(person=person).first()
    if link is None:
        return False
    with transaction.atomic():
        audit.record(audit.ADDRESS_CLEARED, user, outcome="removed", link=link)
        Address.objects.filter(pk=link.address_id).delete()   # the link cascades
        PersonAddress.objects.filter(pk=link.pk).delete()
    return True


# ---------------------------------------------------------------------------
# A community's headquarters and zone
# ---------------------------------------------------------------------------

def _link_of(community):
    return (CommunityHeadquarters.objects.select_related("address", "zone")
            .filter(community=community).first())


def _drop_empty(link):
    if link is not None and link.address_id is None and link.zone_id is None:
        CommunityHeadquarters.objects.filter(pk=link.pk).delete()


def save_headquarters(user, community, *, lat, lng, name, note, op):
    data = _clean_point(lat, lng, name, note)
    op = charging.clean_op(op)
    body = {"community": community.pk, "headquarters": data}
    link = _link_of(community)
    current = link.address if link is not None else None
    if charging.known(user, op, body) or point_state(current) == data:
        return point_state(current), False
    metric = metrics.NOTE if current is not None else metrics.PIN
    charging.afford(user, metric)

    def save():
        row = link or CommunityHeadquarters.objects.create(community=community)
        address = _write_point(current, data, user)
        if row.address_id != address.pk:
            row.address = address
            row.save(update_fields=["address"])
        return row

    saved, charged, settled = _settle(
        user, metric, op, body, CHANGE_LABEL if current is not None else POINT_LABEL,
        save, lambda: _link_of(community))
    if not settled:
        return point_state(saved.address if saved is not None else None), False
    audit.record(audit.HEADQUARTERS_SAVED, user, metric=metric, community=community,
                 amount=charging.amount_of(charged), outcome="charged", link=saved)
    return point_state(saved.address if saved is not None else None), True


def clear_headquarters(user, community) -> bool:
    link = _link_of(community)
    if link is None or link.address_id is None:
        return False
    with transaction.atomic():
        audit.record(audit.HEADQUARTERS_CLEARED, user, community=community,
                     outcome="removed", link=link)
        Address.objects.filter(pk=link.address_id).delete()     # SET_NULL on the link
        link.refresh_from_db()
        _drop_empty(link)
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
    link = _link_of(community)
    current = link.zone if link is not None else None
    if charging.known(user, op, body) or zone_state(current) == data:
        return zone_state(current), False
    metric = metrics.NOTE if current is not None else metrics.ZONE
    charging.afford(user, metric)

    def save():
        row = link or CommunityHeadquarters.objects.create(community=community)
        zone = current or Zone(created_by=user)
        zone.name, zone.description, zone.outline = data["name"], data["description"], polygon
        zone.save()
        if row.zone_id != zone.pk:
            row.zone = zone
            row.save(update_fields=["zone"])
        return row

    saved, charged, settled = _settle(
        user, metric, op, body, CHANGE_LABEL if current is not None else ZONE_LABEL,
        save, lambda: _link_of(community))
    if not settled:
        return zone_state(saved.zone if saved is not None else None), False
    audit.record(audit.ZONE_SAVED, user, metric=metric, community=community,
                 amount=charging.amount_of(charged), outcome="charged", link=saved)
    return zone_state(saved.zone if saved is not None else None), True


def clear_zone(user, community) -> bool:
    link = _link_of(community)
    if link is None or link.zone_id is None:
        return False
    with transaction.atomic():
        audit.record(audit.ZONE_CLEARED, user, community=community,
                     outcome="removed", link=link)
        Zone.objects.filter(pk=link.zone_id).delete()
        link.refresh_from_db()
        _drop_empty(link)
    return True
