from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.company.models import (
    Company,
    OwnershipEvent,
    OwnershipEventType,
    Party,
    ShareClass,
    ShareHolding,
)


def _decimal(value):
    value = Decimal(str(value))
    if value <= 0:
        raise ValidationError("Share units must be positive.")
    return value


def _same_company(company: Company, *objects):
    for obj in objects:
        obj_company = getattr(obj, "company", None)
        if obj_company is None and hasattr(obj, "share_class"):
            obj_company = obj.share_class.company
        if obj_company != company:
            raise ValidationError("All ownership objects must belong to the same company.")


def _live_holding(party: Party, share_class: ShareClass):
    return (
        ShareHolding.objects
        .select_for_update()
        .filter(party=party, share_class=share_class, until__isnull=True)
        .first()
    )


def _replace_holding(*, party, share_class, units, effective_on, event, note=""):
    current = _live_holding(party, share_class)
    if current:
        current.until = effective_on
        current.note = current.note or note
        current.save(update_fields=["until", "note", "updated_at"])
    if units > 0:
        return ShareHolding.objects.create(
            party=party,
            share_class=share_class,
            units=units,
            since=effective_on,
            source_event=event,
            note=note,
        )
    return None


def issue_shares(*, company, target_party, share_class, units, recorded_by=None,
                 effective_on=None, authority_reference="", evidence_ref="",
                 evidence_hash="", note=""):
    units = _decimal(units)
    effective_on = effective_on or timezone.localdate()
    _same_company(company, target_party, share_class)
    with transaction.atomic():
        current = _live_holding(target_party, share_class)
        new_units = units + (current.units if current else Decimal("0"))
        event = OwnershipEvent.objects.create(
            company=company,
            event_type=OwnershipEventType.ISSUE,
            target_party=target_party,
            target_share_class=share_class,
            target_units=units,
            effective_on=effective_on,
            authority_reference=authority_reference,
            evidence_ref=evidence_ref,
            evidence_hash=evidence_hash,
            recorded_by=recorded_by,
            note=note,
        )
        _replace_holding(
            party=target_party,
            share_class=share_class,
            units=new_units,
            effective_on=effective_on,
            event=event,
            note=note,
        )
        return event


def transfer_shares(*, company, source_party, target_party, share_class, units,
                    recorded_by=None, effective_on=None, authority_reference="",
                    evidence_ref="", evidence_hash="", note=""):
    units = _decimal(units)
    effective_on = effective_on or timezone.localdate()
    if source_party == target_party:
        raise ValidationError("Source and target party must differ.")
    _same_company(company, source_party, target_party, share_class)
    with transaction.atomic():
        source_current = _live_holding(source_party, share_class)
        if source_current is None or source_current.units < units:
            raise ValidationError("The source party does not hold enough shares.")
        target_current = _live_holding(target_party, share_class)
        source_new = source_current.units - units
        target_new = units + (target_current.units if target_current else Decimal("0"))
        event = OwnershipEvent.objects.create(
            company=company,
            event_type=OwnershipEventType.TRANSFER,
            source_party=source_party,
            target_party=target_party,
            source_share_class=share_class,
            target_share_class=share_class,
            source_units=units,
            target_units=units,
            effective_on=effective_on,
            authority_reference=authority_reference,
            evidence_ref=evidence_ref,
            evidence_hash=evidence_hash,
            recorded_by=recorded_by,
            note=note,
        )
        _replace_holding(
            party=source_party,
            share_class=share_class,
            units=source_new,
            effective_on=effective_on,
            event=event,
            note=note,
        )
        _replace_holding(
            party=target_party,
            share_class=share_class,
            units=target_new,
            effective_on=effective_on,
            event=event,
            note=note,
        )
        return event


def split_share_class(*, company, source_share_class, target_share_class, numerator,
                      denominator=1, recorded_by=None, effective_on=None,
                      authority_reference="", evidence_ref="", evidence_hash="",
                      note=""):
    numerator = _decimal(numerator)
    denominator = _decimal(denominator)
    effective_on = effective_on or timezone.localdate()
    _same_company(company, source_share_class, target_share_class)
    with transaction.atomic():
        live = list(
            ShareHolding.objects.select_for_update()
            .filter(share_class=source_share_class, until__isnull=True)
            .select_related("party")
            .order_by("pk")
        )
        event = OwnershipEvent.objects.create(
            company=company,
            event_type=OwnershipEventType.SPLIT,
            source_share_class=source_share_class,
            target_share_class=target_share_class,
            source_units=denominator,
            target_units=numerator,
            effective_on=effective_on,
            authority_reference=authority_reference,
            evidence_ref=evidence_ref,
            evidence_hash=evidence_hash,
            recorded_by=recorded_by,
            note=note or f"Split ratio {numerator}:{denominator}",
        )
        for holding in live:
            new_units = holding.units * numerator / denominator
            existing_target = None
            if target_share_class != source_share_class:
                existing_target = _live_holding(holding.party, target_share_class)
            if existing_target:
                new_units += existing_target.units
            _replace_holding(
                party=holding.party,
                share_class=source_share_class,
                units=Decimal("0"),
                effective_on=effective_on,
                event=event,
                note=note,
            )
            _replace_holding(
                party=holding.party,
                share_class=target_share_class,
                units=new_units,
                effective_on=effective_on,
                event=event,
                note=note,
            )
        return event


def record_shareholding(*, company, party, share_class, units, recorded_by=None,
                        effective_on=None, authority_reference="", evidence_ref="",
                        evidence_hash="", note=""):
    """Set one shareholder's live position exactly, through an append-only event."""
    units = _decimal(units)
    effective_on = effective_on or timezone.localdate()
    _same_company(company, party, share_class)
    with transaction.atomic():
        current = _live_holding(party, share_class)
        previous_units = current.units if current else Decimal("0")
        event = OwnershipEvent.objects.create(
            company=company,
            event_type=OwnershipEventType.CORRECTION,
            source_party=party,
            target_party=party,
            source_share_class=share_class,
            target_share_class=share_class,
            source_units=previous_units,
            target_units=units,
            effective_on=effective_on,
            authority_reference=authority_reference,
            evidence_ref=evidence_ref,
            evidence_hash=evidence_hash,
            recorded_by=recorded_by,
            note=note,
        )
        _replace_holding(
            party=party,
            share_class=share_class,
            units=units,
            effective_on=effective_on,
            event=event,
            note=note,
        )
        return event
