# toto.assets — Lightweight Asset Ledger

A native Django/PostgreSQL asset ledger inspired by Algorand Standard Assets (ASA).
No blockchain libraries, no Algorand SDK, no smart contracts.

## Purpose

An admin mints an `Asset` (ticker, decimals, total supply). Users hold balances as `AssetHolding` records. Every transfer, mint, or burn posts an immutable `LedgerEntry` pair (debit + credit) under a `LedgerTransaction`. Once posted, the transaction is sealed; corrections go through an explicit reversal. A SHA-256 `LedgerHash` chain links every posted transaction — tampering with any entry breaks the chain and can be detected by `verify_hash_chain()`. `Obligation` records model debts that will be settled via future transactions. `Contract` / `Agreement` are the runtime layer for Lapis smart contracts executed by financial instruments.

## Concepts

| ASA concept | This ledger |
|---|---|
| Asset | `Asset` model |
| Account | `LedgerAccount` model |
| Asset holding | `AssetHolding` model |
| Transaction | `LedgerTransaction` + `LedgerEntry` rows |
| Clawback / freeze | Not implemented |

## Amounts

All amounts are stored as **integer base units** internally, exactly like Algorand.

```python
display_amount = Decimal("12.34")
decimals = 2
base_units = 1234  # what is stored
```

Helpers:
```python
from toto.assets.models import to_base_units, from_base_units

to_base_units(Decimal("12.34"), 2)  # → 1234
from_base_units(1234, 2)            # → Decimal("12.34")
```

Never use `float`.

## Ledger entries

Each transaction produces balanced `LedgerEntry` rows:

- Positive `amount_base_units` → account **receives** units
- Negative `amount_base_units` → account **sends** units

The sum of all entries per asset across all transactions is always zero.
Entries are **immutable** — they cannot be edited or deleted after creation.

## Corrections

Posted transactions are immutable. To correct a mistake, call `reverse_transaction()`.
This creates a new `LedgerTransaction` with `transaction_type="reversal"` and opposite entries.

## Hash chain

Every posted transaction gets a `LedgerHash` record containing:
- SHA-256 hash of the transaction data + entries
- Previous hash (links records into a chain)

This provides tamper evidence. Call `verify_hash_chain()` to re-verify the entire chain.

## Swapping the engine

To replace the Django backend (e.g., with Algorand in the future):

1. Subclass `toto.assets.backend.LedgerBackend`
2. Override `create_asset`, `transfer_asset`, `reverse_transaction`
3. Set `ASSETS_BACKEND = "myapp.MyBackend"` in settings

```python
from toto.assets.backend import get_backend

backend = get_backend()
asset = backend.create_asset(name="Token", ...)
```

## Quick example

```python
from decimal import Decimal
from toto.assets.models import LedgerAccount
from toto.assets.backend import get_backend

reserve = LedgerAccount.objects.create(code="reserve", name="Reserve", account_type="reserve")
alice   = LedgerAccount.objects.create(code="alice",   name="Alice",   account_type="user")
bob     = LedgerAccount.objects.create(code="bob",     name="Bob",     account_type="user")

backend = get_backend()

asset = backend.create_asset(
    name="Spectrum Credit", unit_name="SPC",
    total_supply=Decimal("1000000"), decimals=2,
    reserve_account=reserve, reference="create-spc",
)

tx = backend.transfer_asset(
    asset=asset, sender_account=reserve, receiver_account=alice,
    amount=Decimal("100.00"), reference="txfr-spc-alice-001",
)

backend.reverse_transaction(
    transaction=tx, reference="rev-txfr-spc-alice-001",
    description="Mistaken transfer reversal",
)
```

## Dependencies

- `inventory` — Physical asset types linked via Contract/Agreement runtime
- `people` — Person as account holder identity

## Enigma Wallet API

Single aggregated read-only endpoint combining balances, pending charges, and open invoices.

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/assets/api/wallet/summary/` | Wallet summary for the authenticated user |

### Response shape
```json
{
  "accounts": [{"code", "name", "account_type", "holdings": [{"asset_name", "asset_unit", "balance_display"}]}],
  "pending_charges": [{"metric_code", "quantity", "unit", "tariff_name", "asset_unit", "amount_display", "occurred_at"}],
  "open_invoices": [{"id", "title", "amount", "currency", "status", "due_date", "issued_by_name"}],
  "totals": {"total_pending_by_asset": {"UNIT": "amount"}, "total_open_invoice_amount": "0.00"}
}
```

- `accounts` — `LedgerAccount` rows where `user = request.user`
- `pending_charges` — `UsageRecord` rows with status `pending` or `rated`
- `open_invoices` — `Invoice` rows with status `pending` or `overdue`

### Testing
```bash
cd portal && python manage.py test toto.assets.tests_wallet_api
```
