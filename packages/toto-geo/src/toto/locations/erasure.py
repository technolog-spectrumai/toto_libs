"""What erasing an account removes from the map (2026-10-01, 37c.21; here
since 2026-10-04 — it was part of ``toto.core.erasure``, which calls this
where the app is installed).

A member's home is a ``Home`` row from the person to an address; the row goes
with the person, the address would stay on the map linked to nobody. So the
erase removes the home pin too, and the addresses the member added that
nothing else uses. A pin another person lives at, or a place is built on,
stays.
"""

from __future__ import annotations

from django.db import router, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError

from toto.locations.models import Address, CommunitySeat, EventPlace, Home


def _linked(address, *, but=None) -> bool:
    """Is it somebody else's home, an event's place or a community's seat?
    Those links cascade with the address, so the collector below would not
    count them as a use."""
    homes = Home.objects.filter(address=address)
    if but is not None:
        homes = homes.exclude(pk=but.pk)
    return (homes.exists() or EventPlace.objects.filter(address=address).exists()
            or CommunitySeat.objects.filter(address=address).exists())


def _used_elsewhere(address, *, but=None) -> bool:
    """Does anything but ``but`` (the Home of the person being erased) still
    point at it? Another home, an event or a community (``_linked``), a place
    built on it (PROTECT), a route or a territory's capital (SET_NULL) — the
    collector asks."""
    if _linked(address, but=but):
        return True
    collector = Collector(using=router.db_for_write(type(address)))
    try:
        collector.collect([address])
    except (ProtectedError, RestrictedError):
        return True
    for (fk, _value), batches in collector.field_updates.items():
        for batch in batches:
            rows = list(batch) if not isinstance(batch, (list, tuple)) else batch
            if any(not (but is not None and type(row) is type(but) and row.pk == but.pk)
                   for row in rows):
                return True
    return False


def addresses_of(user, person) -> tuple[int | None, list[int]]:
    """The home pin (when no one else lives there and nothing is built on it)
    and the addresses they added that nothing else uses."""
    home = None
    own_home = Home.objects.filter(person=person).first() if person is not None else None
    if own_home is not None:
        pin = own_home.address
        if not _home_kept(pin, person):
            home = pin.pk
    own = [address.pk for address in Address.objects.filter(created_by=user).exclude(pk=home)
           if not _used_elsewhere(address, but=own_home)]
    return home, own


def _home_kept(pin, person) -> bool:
    """A home pin stays only when another person lives there or a place is
    built on it — an event or a route at their home loses its address."""
    if Home.objects.filter(address=pin).exclude(person=person).exists():
        return True
    collector = Collector(using=router.db_for_write(type(pin)))
    try:
        collector.collect([pin])
    except (ProtectedError, RestrictedError):
        return True
    return False


def delete_addresses(pks) -> None:
    """Inside the erase's transaction, once the account (and its person, and
    with it the Home row) is gone."""
    for pk in pks:
        try:
            with transaction.atomic():
                Address.objects.filter(pk=pk).delete()
        except (ProtectedError, RestrictedError):
            continue                       # built on meanwhile: it stays
