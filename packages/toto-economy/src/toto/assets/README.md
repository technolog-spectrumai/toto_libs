# toto.assets — Lightweight Asset Ledger

A native Django/PostgreSQL asset ledger inspired by Algorand Standard Assets (ASA).
No blockchain libraries, no Algorand SDK, no smart contracts.

## Purpose

An admin mints an `Asset` (ticker, decimals, total supply). Users hold balances as `AssetHolding` records. Every transfer, mint, or burn posts an immutable `LedgerEntry` pair (debit + credit) under a `LedgerTransaction`. Once posted, the transaction is sealed; corrections go through an explicit reversal. A SHA-256 `LedgerHash` chain links every posted transaction — tampering with any entry breaks the chain and can be detected by `verify_hash_chain()`.

> The Lapis smart-contract layer (`Contract`/`Agreement` + the YAML VM) and the
> `Obligation` debt model were pulled out on 2026-07-31 as immature — see
> `toto_libs/limbo/lapis/`. This app is now the ledger core only.

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

- `gervazy` — encrypted private keys behind transaction signing and the wallet PIN
- `people` — Person as account holder identity

## Revived into zenobia, July 2026

This app came back from `toto_libs/limbo` and now lives in the zenobia host tree
(gated by `BUILD_ASSETS`). What changed against the limbo original:

- `Tokenization` is gone — it FK'd `inventory.RealWorldObject`, and `inventory`
  is still parked. Anchoring assets to physical objects returns with that app.
- The ingress seeder mints exactly two currencies, **ASR** (Assarion) and
  **TPLN** (Toto Złoty). The gold/silver bullion framing (AUR/Aureus, the
  virtual bullion vault, the chest tokenizations) and the demo TUSD/TEUR seeds
  were all dropped.
- `get_stablecoin_for_currency()` is now `get_currency_asset()`.
- The wallet PIN module moved in from `bazaar` (`toto.assets.wallet_pin` plus
  the `WalletPin` model); lifecycle primitives that re-exported `toto.claims`
  were dropped along with the claims-owned templates.
- Migration history was squashed to a fresh `0001_initial`, per the revival
  policy in aurelian's README.

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
