import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from toto.assets.models import Asset, LedgerAccount, LedgerTransaction
from toto.assets.services.assets import transfer_asset


def person_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.community_profile
    except Exception:
        return None


def geometry_json(geometry):
    if not geometry:
        return None
    return json.loads(geometry.geojson)


def detection_map_feature(detection):
    geometry = detection.map_geometry
    if not geometry:
        return None
    return {
        "id": str(detection.pk),
        "title": detection.title,
        "type": detection.get_detection_type_display(),
        "severity": detection.severity,
        "status": detection.get_status_display(),
        "location": detection.location_label or "",
        "url": detection.get_absolute_url() if hasattr(detection, "get_absolute_url") else "",
        "geometry": geometry_json(geometry),
    }


def get_payment_asset(payment):
    if payment.asset_id:
        return payment.asset
    if payment.currency_id and payment.currency.asset_id:
        return payment.currency.asset
    bounty = payment.claim.bounty
    if bounty.reward_asset_id:
        return bounty.reward_asset
    if bounty.reward_currency_id and bounty.reward_currency.asset_id:
        return bounty.reward_currency.asset
    raise ValidationError("This bounty payment is not connected to an active payment asset.")


def get_payment_source_account(payment):
    bounty = payment.claim.bounty
    account = payment.ledger_account or bounty.reward_ledger_account or bounty.board.ledger_account
    if not account:
        raise ValidationError("This bounty payment has no source ledger account.")
    return account


def get_default_receiver_account(payment):
    if payment.receiver_ledger_account_id:
        return payment.receiver_ledger_account
    claim_user = payment.claim.user
    if not claim_user:
        return None
    return (
        LedgerAccount.objects
        .filter(user=claim_user, active=True)
        .order_by("pk")
        .first()
    )


def settle_bounty_payment(*, payment, receiver_account=None, reference="", note="", paid_by=None):
    with transaction.atomic():
        payment = payment.__class__.objects.select_for_update().select_related(
            "asset",
            "currency__asset",
            "claim__bounty__reward_asset",
            "claim__bounty__reward_currency__asset",
            "claim__bounty__reward_ledger_account",
            "claim__bounty__board__ledger_account",
            "claim__user",
            "receiver_ledger_account",
        ).get(pk=payment.pk)

        if payment.is_settled:
            raise ValidationError("This bounty payment is already settled.")

        asset = get_payment_asset(payment)
        source_account = get_payment_source_account(payment)
        receiver_account = receiver_account or get_default_receiver_account(payment)
        if not receiver_account:
            raise ValidationError("Choose a receiver ledger account before settling payment.")

        amount = Decimal(payment.amount)
        if amount <= 0:
            raise ValidationError("Payment amount must be positive.")

        reference = reference or f"bounty-payment-{payment.pk}"
        if LedgerTransaction.objects.filter(reference=reference).exists():
            raise ValidationError(f"Ledger transaction reference '{reference}' already exists.")

        tx = transfer_asset(
            asset=asset,
            sender_account=source_account,
            receiver_account=receiver_account,
            amount=amount,
            reference=reference,
            description=f"Bounty payment for {payment.claim.bounty.title}",
            metadata={
                "source": "detections.bounty_payment",
                "payment_id": payment.pk,
                "claim_id": payment.claim_id,
                "bounty_id": payment.claim.bounty_id,
            },
        )

        payment.asset = asset
        payment.ledger_account = source_account
        payment.receiver_ledger_account = receiver_account
        payment.ledger_tx_reference = tx.reference
        if paid_by:
            payment.paid_by = paid_by
        if note:
            payment.note = note
        payment.is_settled = True
        payment.save(update_fields=[
            "asset",
            "ledger_account",
            "receiver_ledger_account",
            "ledger_tx_reference",
            "paid_by",
            "note",
            "is_settled",
        ])

        payment.claim.status = "paid"
        payment.claim.save(update_fields=["status", "updated_at"])
        return tx
