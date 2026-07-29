import hashlib
import json

from .models import LedgerHash, LedgerTransaction


def calculate_transaction_hash(transaction: LedgerTransaction, previous_hash: str) -> str:
    entries = list(
        transaction.entries.order_by("id").values(
            "account_id", "asset_id", "amount_base_units"
        )
    )
    data = {
        "reference": transaction.reference,
        "transaction_type": transaction.transaction_type,
        "description": transaction.description,
        "source_type": transaction.source_type,
        "source_id": transaction.source_id,
        "asset_id": transaction.asset_id,
        "metadata": transaction.metadata,
        "created_at": transaction.created_at.isoformat(),
        "entries": [
            {
                "account_id": e["account_id"],
                "asset_id": e["asset_id"],
                "amount_base_units": e["amount_base_units"],
            }
            for e in entries
        ],
        "previous_hash": previous_hash,
    }
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def attach_hash(transaction: LedgerTransaction) -> LedgerHash:
    last = LedgerHash.objects.select_for_update().order_by("-id").first()
    previous_hash = last.hash if last else ""
    hash_value = calculate_transaction_hash(transaction, previous_hash)
    return LedgerHash.objects.create(
        transaction=transaction,
        previous_hash=previous_hash,
        hash=hash_value,
    )


def verify_hash_chain() -> bool:
    previous_hash = ""
    for record in LedgerHash.objects.select_related("transaction").order_by("id"):
        expected = calculate_transaction_hash(record.transaction, previous_hash)
        if record.hash != expected or record.previous_hash != previous_hash:
            return False
        previous_hash = record.hash
    return True
