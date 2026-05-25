# toto.instruments

## Purpose

Financial instruments wrap the `assets` ledger primitives into higher-level financial contracts. Each instrument type creates the appropriate `claims.Entitlement`, `Schedule`, `Condition`, `Allocation`, and `ContractEvent` records as it progresses through its lifecycle. Service classes (one per type) encapsulate all state transitions — views never touch model fields directly. Instruments are how communities structure real economic relationships: a vendor gets paid through escrow, a contributor vests equity, a subscriber is billed monthly, a borrower amortizes a loan.

Django app for deterministic financial instruments:

- Escrow
- Forward contracts
- Future markets and future contracts
- Future margin positions
- Revenue share contracts
- Timelocks
- Vesting contracts
- Staking positions
- Instrument obligations and execution audit log

Loans and insurance are intentionally excluded. Put them in `toto.risk`, because they require credit scoring, underwriting, claim review, and risk decisions.

## Install

Copy `instruments/` to `toto/instruments/` and add the app:

```python
INSTALLED_APPS = [
    ...,
    "toto.instruments",
]
```

Include URLs:

```python
path("instruments/", include("toto.instruments.urls")),
```

Run migrations:

```bash
python manage.py makemigrations instruments
python manage.py migrate
```

## Recommended assets patch

In `toto.assets.models.AccountType`, add:

```python
CONTRACT = "contract", "Contract"
```

Use contract accounts for escrows, margin pools, staking pools, timelock vaults, and vesting pools.

In `TransactionType`, consider adding generic contract transaction types:

```python
CONTRACT_LOCK = "contract_lock", "Contract Lock"
CONTRACT_RELEASE = "contract_release", "Contract Release"
CONTRACT_SETTLEMENT = "contract_settlement", "Contract Settlement"
CONTRACT_PAYOUT = "contract_payout", "Contract Payout"
```

Optionally extend `Obligation` with:

```python
source_type = models.CharField(max_length=100, blank=True)
source_id = models.CharField(max_length=255, blank=True)
metadata = models.JSONField(default=dict, blank=True)
```

The services in this app detect those optional `Obligation` fields and will still work if they are absent.

## Architecture

`assets` remains the settlement and accounting layer. This app never edits `AssetHolding` directly. All asset movement goes through `get_backend().transfer_asset(...)`.

`instruments` defines deterministic financial contracts and records their execution history.

`risk` should hold loans, insurance, credit checks, underwriting decisions, and claims.

## Dependencies

- `assets` — Financial instruments operate on Asset and LedgerAccount
