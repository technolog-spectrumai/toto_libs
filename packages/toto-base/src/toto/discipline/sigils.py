"""Sigils: made, handed to a person, taken back."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import audit
from .models import Sigil, SigilAction, SigilEvent


def create(name: str, regen_per_hour, *, by=None, image=None, description: str = "") -> Sigil:
    sigil = Sigil(name=name.strip(), regen_per_hour=regen_per_hour,
                  description=description.strip(), issued_by=by)
    if image:
        sigil.image = image
    sigil.full_clean()
    sigil.save()
    audit.sigil(sigil, "SIGIL_CREATED", by=by)
    return sigil


@transaction.atomic
def assign(sigil: Sigil, user, *, by=None, note: str = "") -> Sigil:
    """Hand the sigil to ``user``. A sigil has one holder: whoever held it
    stops holding it in the same step."""
    sigil = Sigil.objects.select_for_update().get(pk=sigil.pk)
    if sigil.active and sigil.holder_id == user.pk:
        return sigil
    if sigil.holder_id and sigil.holder_id != user.pk:
        _take(sigil, by=by, note=note)
    sigil.holder, sigil.active = user, True
    sigil.assigned_at, sigil.revoked_at, sigil.revoked_by = timezone.now(), None, None
    sigil.save(update_fields=["holder", "active", "assigned_at", "revoked_at", "revoked_by"])
    SigilEvent.objects.create(sigil=sigil, user=user, action=SigilAction.ASSIGNED, by=by,
                              note=note[:255])
    audit.sigil(sigil, "SIGIL_ASSIGNED", by=by, holder=user)
    return sigil


@transaction.atomic
def revoke(sigil: Sigil, *, by=None, note: str = "") -> Sigil:
    """Take the sigil back. It generates nothing from now on; the mana it
    generated stays where it is."""
    sigil = Sigil.objects.select_for_update().get(pk=sigil.pk)
    if sigil.holder_id:
        _take(sigil, by=by, note=note)
    elif sigil.active:
        audit.sigil(sigil, "SIGIL_REVOKED", by=by)
    sigil.active, sigil.revoked_at, sigil.revoked_by = False, timezone.now(), by
    sigil.save(update_fields=["holder", "active", "revoked_at", "revoked_by"])
    return sigil


def _take(sigil: Sigil, *, by, note: str) -> None:
    holder = sigil.holder
    SigilEvent.objects.create(sigil=sigil, user=holder, action=SigilAction.REVOKED, by=by,
                              note=note[:255])
    sigil.holder = None
    audit.sigil(sigil, "SIGIL_REVOKED", by=by, holder=holder)
