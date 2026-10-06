"""What erasing an account removes from geography, and what "Download my
data" holds of it.

Simpler than the parked map's: an address here has exactly one link. A
member's point is a ``PersonAddress`` row from their person to an
``Address``; both go with the account. ``toto.core.erasure`` calls this
where the app is installed (a lazy import, no package-graph edge).

A community's headquarters and zone are the community's, not their
author's: they stay, and ``created_by`` is emptied by the database
(SET_NULL).

Since stage 64 the same holds for what a member contributed to a community:
their pins and zones stay on the community's map and their comments in the
threads, each with no author (``author`` is SET_NULL on ``CommunityPin``,
``CommunityZone`` and ``comments.Comment``), as comments already did. A
member who wants them gone deletes them first, from ``me/contributions/``.
"Download my data" holds all three (``contribution_rows``).
"""

from __future__ import annotations

from .models import Address, PersonAddress


def addresses_of(user, person) -> list[int]:
    """The ids of the ``Address`` rows that go with this account: the
    member's own point."""
    if person is None or not getattr(person, "pk", None):
        return []
    return list(PersonAddress.objects.filter(person=person)
                .values_list("address_id", flat=True))


def delete_addresses(pks) -> None:
    """Inside the erase's transaction. The link's ``post_delete`` receiver
    has usually deleted the row already (the person went, the link with
    it); this makes sure none is left."""
    if pks:
        Address.objects.filter(pk__in=list(pks)).delete()


def export_rows(user, person) -> list[dict]:
    """The member's own point for "Download my data": where, its name and
    its note."""
    from .shapes import pair_of

    rows = []
    if person is None or not getattr(person, "pk", None):
        return rows
    for link in PersonAddress.objects.select_related("address").filter(person=person):
        lat, lng = pair_of(link.address.point)
        rows.append({"latitude": lat, "longitude": lng, "name": link.address.name,
                     "note": link.address.note,
                     "saved": link.address.created_at.isoformat(),
                     "changed": link.address.updated_at.isoformat()})
    return rows


def contribution_rows(user) -> dict[str, list[dict]]:
    """The member's pins, zones and comments for "Download my data"."""
    from .locations import own_comments
    from .models import CommunityPin, CommunityZone
    from .shapes import corners_of, pair_of

    pins, zones, comments = [], [], []
    for pin in CommunityPin.objects.select_related("community", "address").filter(author=user):
        lat, lng = pair_of(pin.address.point)
        pins.append({"community": str(pin.community.name), "latitude": lat, "longitude": lng,
                     "name": pin.address.name, "address": pin.address.postal_address,
                     "note": pin.address.note, "hidden": pin.is_hidden,
                     "saved": pin.created_at.isoformat()})
    for row in CommunityZone.objects.select_related("community", "zone").filter(author=user):
        zones.append({"community": str(row.community.name), "name": row.zone.name,
                      "description": row.zone.description,
                      "outline": str(corners_of(row.zone.outline)),
                      "hidden": row.is_hidden, "saved": row.created_at.isoformat()})
    for kind, row, comment in own_comments(user):
        comments.append({"community": str(row.community.name), "on": kind,
                         "name": row.address.name if kind == "pin" else row.zone.name,
                         "text": comment.body, "written": comment.created_at.isoformat()})
    return {"pins": pins, "zones": zones, "comments": comments}
