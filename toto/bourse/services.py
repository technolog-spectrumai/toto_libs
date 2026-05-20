from django.core.exceptions import ValidationError
from django.db import transaction

from toto.assets.queries import get_asset_balance_display
from toto.assets.services.assets import get_exchange_fee_account, transfer_asset

from .models import AssetExchangeRequest, ExchangeRequestStatus


def accept_exchange_request(*, exchange_request, counterparty_account, response_note: str = ""):
    from django.utils import timezone

    with transaction.atomic():
        req = AssetExchangeRequest.objects.select_for_update().select_related(
            "requester_account",
            "counterparty_account",
            "offer_asset",
            "request_asset",
        ).get(pk=exchange_request.pk)

        if req.status != ExchangeRequestStatus.PENDING:
            raise ValidationError("This exchange request is no longer pending.")
        if not counterparty_account.user_id:
            raise ValidationError("Counterparty account must belong to a user.")
        if counterparty_account.user_id == req.requester_id:
            raise ValidationError("You cannot accept your own exchange proposal.")
        if req.counterparty_id and counterparty_account.user_id != req.counterparty_id:
            raise ValidationError("Counterparty account does not belong to the recipient.")

        requester_total = req.requester_total_display
        requester_balance = get_asset_balance_display(req.offer_asset, req.requester_account)
        if requester_balance < requester_total:
            raise ValidationError(
                f"Requester has insufficient {req.offer_asset.unit_name}. "
                f"Needs {requester_total}, has {requester_balance}."
            )

        counterparty_balance = get_asset_balance_display(req.request_asset, counterparty_account)
        if counterparty_balance < req.request_amount_display:
            raise ValidationError(
                f"You have insufficient {req.request_asset.unit_name}. "
                f"Needs {req.request_amount_display}, has {counterparty_balance}."
            )

        offer_ref = f"exchange-{req.pk}-offer"
        request_ref = f"exchange-{req.pk}-request"
        transfer_asset(
            asset=req.offer_asset,
            sender_account=req.requester_account,
            receiver_account=counterparty_account,
            amount=req.offer_amount_display,
            reference=offer_ref,
            description=f"Bourse exchange {req.pk}: offered asset transfer",
            metadata={"exchange_request": req.pk, "bourse_trade": req.pk},
        )
        transfer_asset(
            asset=req.request_asset,
            sender_account=counterparty_account,
            receiver_account=req.requester_account,
            amount=req.request_amount_display,
            reference=request_ref,
            description=f"Bourse exchange {req.pk}: requested asset transfer",
            metadata={"exchange_request": req.pk, "bourse_trade": req.pk},
        )

        fee_ref = ""
        if req.commission_amount_display > 0:
            fee_ref = f"exchange-{req.pk}-fee"
            transfer_asset(
                asset=req.offer_asset,
                sender_account=req.requester_account,
                receiver_account=get_exchange_fee_account(),
                amount=req.commission_amount_display,
                reference=fee_ref,
                description=f"Bourse exchange {req.pk}: commission",
                metadata={"exchange_request": req.pk, "bourse_trade": req.pk, "commission_percent": str(req.commission_percent)},
            )

        req.counterparty_account = counterparty_account
        if req.counterparty_id is None:
            req.counterparty_id = counterparty_account.user_id
        req.status = ExchangeRequestStatus.ACCEPTED
        req.offer_tx_reference = offer_ref
        req.request_tx_reference = request_ref
        req.commission_tx_reference = fee_ref
        req.response_note = response_note
        req.responded_at = timezone.now()
        req.save(update_fields=[
            "counterparty_account",
            "counterparty",
            "status",
            "offer_tx_reference",
            "request_tx_reference",
            "commission_tx_reference",
            "response_note",
            "responded_at",
            "updated_at",
        ])
        return req


def reject_exchange_request(*, exchange_request, response_note: str = ""):
    from django.utils import timezone

    with transaction.atomic():
        req = AssetExchangeRequest.objects.select_for_update().get(pk=exchange_request.pk)
        if req.status != ExchangeRequestStatus.PENDING:
            raise ValidationError("This exchange request is no longer pending.")
        req.status = ExchangeRequestStatus.REJECTED
        req.response_note = response_note
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "response_note", "responded_at", "updated_at"])
        return req


def cancel_exchange_request(*, exchange_request, response_note: str = ""):
    from django.utils import timezone

    with transaction.atomic():
        req = AssetExchangeRequest.objects.select_for_update().get(pk=exchange_request.pk)
        if req.status != ExchangeRequestStatus.PENDING:
            raise ValidationError("This exchange request is no longer pending.")
        req.status = ExchangeRequestStatus.CANCELLED
        req.response_note = response_note
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "response_note", "responded_at", "updated_at"])
        return req
