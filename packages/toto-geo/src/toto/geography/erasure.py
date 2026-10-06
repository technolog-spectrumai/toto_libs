"""What erasing an account removes from geography, and what "Download my
data" holds of it.

Simpler than the parked map's: an address here has exactly one link. A
member's point is a ``PersonAddress`` row from their person to an
``Address``; both go with the account. ``toto.core.erasure`` calls this
where the app is installed (a lazy import, no package-graph edge).

A community's headquarters and zone are the community's, not their
author's: they stay, and ``created_by`` is emptied by the database
(SET_NULL).
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
