from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from toto.assets.models import Asset, LedgerAccount
from toto.assets.services.assets import distribute_asset, transfer_asset


def purchase_storage_tokens(
    *,
    payer_account: LedgerAccount,
    storage_asset: Asset,
    payment_asset: Asset,
    token_amount: Decimal,
    price_per_token: Decimal,
    revenue_account: LedgerAccount,
    reference: str,
    username: str = "",
    signer=None,
) -> dict:
    """
    Atomic purchase: debit payment_asset from payer, credit storage_asset to payer.
    Returns a dict with token_amount and payment_amount for display.
    """
    token_amount = Decimal(str(token_amount))
    price_per_token = Decimal(str(price_per_token))

    if token_amount <= 0:
        raise ValidationError("Token amount must be positive.")

    payment_amount = (token_amount * price_per_token).quantize(
        Decimal(1).scaleb(-payment_asset.decimals)
    )

    with db_transaction.atomic():
        if payment_amount > 0:
            transfer_asset(
                asset=payment_asset,
                sender_account=payer_account,
                receiver_account=revenue_account,
                amount=payment_amount,
                reference=f"{reference}-pay",
                description=f"Payment for {token_amount} {storage_asset.unit_name}",
                pre_post_hook=signer,
            )
        distribute_asset(
            asset=storage_asset,
            recipient_account=payer_account,
            amount=token_amount,
            reference=f"{reference}-recv",
            description=f"Storage token purchase by {username or payer_account.code}",
            pre_post_hook=signer,
        )

    return {"token_amount": token_amount, "payment_amount": payment_amount}
