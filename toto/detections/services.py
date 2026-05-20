import json
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.models import Asset, LedgerAccount, LedgerTransaction
from toto.assets.services.assets import transfer_asset
from toto.kanban.models import Campaign, Column, Mission, Project, Task
from toto.people.models import Person


def first_or_create(model, defaults=None, **lookup):
    obj = model.objects.filter(**lookup).order_by("pk").first()
    if obj:
        return obj, False
    params = {**lookup, **(defaults or {})}
    return model.objects.create(**params), True


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


def ensure_detection_mitigation_task(detection, *, owner=None, reviewer=None):
    if detection.mitigation_task_id:
        return detection.mitigation_task

    owner = owner or detection.reported_by or Person.objects.order_by("pk").first()
    if not owner:
        return None

    project, _ = first_or_create(
        Project,
        name="Detection Mitigation",
        defaults={
            "description": "Kanban project for detection mitigation and bounty-backed response work.",
            "owner": owner,
        },
    )
    if owner.user_id:
        project.collaborators.add(owner.user)

    columns = {}
    for name, position, can_add in [
        ("To Do", 1, True),
        ("In Progress", 2, False),
        ("Review", 3, False),
        ("Done", 4, False),
    ]:
        column, _ = first_or_create(
            Column,
            project=project,
            name=name,
            defaults={
                "position": position,
                "can_add_task": can_add,
            },
        )
        if owner.user_id:
            column.auditors.add(owner.user)
        columns[name] = column

    campaign, _ = first_or_create(
        Campaign,
        project=project,
        name="Field Response",
        defaults={
            "description": "Mitigation campaign generated from detections.",
            "start_date": timezone.now().date(),
            "end_date": (timezone.now() + timedelta(days=30)).date(),
            "owner": owner,
            "metadata": {"source": "detections"},
        },
    )
    mission, _ = first_or_create(
        Mission,
        campaign=campaign,
        title="Mitigate Active Detections",
        defaults={
            "description": "Resolve active detection incidents through normal Kanban task flow.",
            "urgency": 3,
            "impact": 3,
            "owner": owner,
            "metadata": {"source": "detections"},
        },
    )

    task = Task.objects.create(
        mission=mission,
        column=columns["To Do"],
        title=f"Mitigate: {detection.title}",
        description=detection.description,
        reviewer=reviewer,
        due_date=(timezone.now() + timedelta(days=3)).date(),
        weight=3 if detection.severity in ("high", "critical") else 2,
        metadata={
            "source": "detection",
            "detection_id": str(detection.pk),
            "severity": detection.severity,
        },
    )
    detection.mitigation_task = task
    detection.save(update_fields=["mitigation_task", "updated_at"])
    return task


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


def complete_task_bounties(task, *, reviewer=None):
    from toto.detections.models import BountyPayment

    payments = []
    for bounty in task.bounties.select_related(
        "board__ledger_account",
        "reward_asset",
        "reward_currency__asset",
        "reward_ledger_account",
    ).all():
        claims = list(bounty.claims.filter(
            status__in=["accepted", "working", "submitted"]
        ).select_related("user", "hunter"))
        for claim in claims:
            if claim.status != "approved":
                claim.status = "approved"
                claim.completed_at = timezone.now()
                claim.save(update_fields=["status", "completed_at", "updated_at"])

            receiver = (
                LedgerAccount.objects
                .filter(user=claim.user, active=True)
                .order_by("pk")
                .first()
                if claim.user_id else None
            )
            payment, _ = BountyPayment.objects.get_or_create(
                claim=claim,
                defaults={
                    "amount": bounty.reward_amount,
                    "currency": bounty.reward_currency,
                    "asset": bounty.reward_asset or (bounty.reward_currency.asset if bounty.reward_currency_id else None),
                    "ledger_account": bounty.reward_ledger_account or bounty.board.ledger_account,
                    "receiver_ledger_account": receiver,
                    "paid_by": reviewer,
                    "note": f"Auto-created when Kanban task #{task.pk} completed.",
                    "is_settled": False,
                },
            )
            payments.append(payment)

        if claims and bounty.status != "completed":
            bounty.status = "completed"
            bounty.save(update_fields=["status", "updated_at"])

    return payments
