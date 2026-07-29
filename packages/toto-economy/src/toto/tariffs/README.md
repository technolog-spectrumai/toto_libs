# toto.tariffs — Prepaid Token Drainage Engine

`toto.tariffs` defines billing rate cards for metered platform services. Users purchase specialised service tokens through the bourse, hold them in ledger accounts, and usage events drain those tokens according to the active tariff. Every drain is an immutable ledger transaction — no balance can go negative and no posted entry can be edited.

---

## The Big Picture

```
User buys AI_TOKEN via bourse
       │
       ▼
AssetHolding (payer account, AI_TOKEN)
       │
usage event arrives
       │
       ▼
tariffs.services.post_usage_record()
  ├── find matching TariffItem(s) by metric_code
  ├── calculate charge in base units
  ├── select_for_update() on payer holding
  ├── check balance ≥ charge (else FAILED)
  ├── write LedgerTransaction (immutable once posted)
  ├── LedgerEntry: -charge from payer account
  └── LedgerEntry: +charge to receiving account (REV-AI, REV-STORAGE, …)
       │
       ▼
UsageRecord.status = POSTED
AssetHolding.balance -= charge
```

---

## Models

### `Tariff`
A named billing plan / rate card.

| Field | Notes |
|---|---|
| `uuid` | Public identifier |
| `name` | Human label |
| `code` | Unique machine key (e.g. `DEFAULT-AI-STORAGE`) |
| `status` | `draft / active / paused / archived` |
| `source_type`, `source_id` | Generic FK — attach to service, agreement, entitlement, etc. |
| `metadata` | Arbitrary JSON |

### `TariffItem`
One billable metric inside a tariff. Codes must be unique within a tariff.

| Field | Notes |
|---|---|
| `code` | Metric key e.g. `ai.input_tokens`, `storage.mb_hour` |
| `charged_asset` | FK → `assets.Asset` (the token drained) |
| `price_per_unit_display` | Human-readable Decimal |
| `price_per_unit_base_units` | Integer in asset base units |
| `unit` | `request / token / input_token / output_token / byte / kb / mb / gb / second / minute / hour / mb_second / mb_minute / mb_hour / gb_hour / node / relationship / custom` |
| `unit_quantity` | Denominator — price applies per this many units (default 1, use 1000 for per-1000-token pricing) |
| `receiving_account` | FK → `assets.LedgerAccount` (revenue account credited on each usage) |
| `minimum_charge_base_units` | Floor charge (0 = no floor) |
| `rounding_mode` | `up / down / nearest` |
| `active` | If False, item is skipped during rating |

### `UsageRecord`
Immutable record of one usage event.

| Field | Notes |
|---|---|
| `uuid` | Public identifier |
| `tariff` | FK → `Tariff` |
| `payer_account` | FK → `assets.LedgerAccount` |
| `metric_code` | e.g. `ai.input_tokens` |
| `quantity` | Decimal amount used |
| `unit` | BillingUnit choice |
| `status` | `pending → rated → posted / failed / reversed` |
| `ledger_transaction` | FK → `assets.LedgerTransaction` (set when posted) |
| `error_message` | Reason if failed |

### `UsageCharge`
A calculated charge line, one per matching TariffItem.

| Field | Notes |
|---|---|
| `usage_record` | FK → `UsageRecord` |
| `tariff_item` | FK → `TariffItem` |
| `charged_asset` | Snapshot of which asset was charged |
| `amount_base_units` | The integer charge |
| `payer_account` | Account debited |
| `receiving_account` | Account credited |

---

## Service Layer

All service functions live in `tariffs.services`.

### `calculate_tariff_charge(tariff, metric_code, quantity, unit) → list[ChargeDraft]`
Pure math — no DB writes. Returns one `ChargeDraft` per active `TariffItem` matching `metric_code`. Applies rounding mode and minimum charge.

```python
drafts = calculate_tariff_charge(tariff, "ai.input_tokens", Decimal("1200"), "input_token")
for d in drafts:
    print(d.tariff_item.code, d.amount_base_units)
```

### `rate_usage_record(usage_record) → list[UsageCharge]`
Calculates and persists `UsageCharge` rows. Marks record as `RATED`. Idempotent — safe to call twice.

### `post_usage_record(usage_record, reference=None, description=None) → LedgerTransaction`
The main posting function.
- Calls `rate_usage_record` if not already rated.
- Uses `select_for_update()` on holdings inside `transaction.atomic()`.
- Rejects posting if any asset balance is insufficient (marks record `FAILED`).
- Creates one `LedgerTransaction` with debit and credit entries.
- Idempotent: uses `tariff-usage-{uuid}` as the unique transaction reference.

```python
try:
    tx = post_usage_record(record)
except ValueError as err:
    # record.status == "failed", record.error_message == str(err)
    pass
```

### `record_and_post_usage(tariff, payer_account, metric_code, quantity, unit, …) → (UsageRecord, LedgerTransaction)`
Convenience: create a `UsageRecord` and immediately post it.

```python
record, tx = record_and_post_usage(
    tariff=tariff,
    payer_account=user_account,
    metric_code="ai.input_tokens",
    quantity=Decimal("1200"),
    unit="input_token",
    source_type="conversation",
    source_id=str(conversation.uuid),
)
```

### `simulate_tariff(tariff, usage_payload) → dict`
Dry run — no DB writes. Returns charge lines and totals by asset. Used by the `/tariffs/<uuid>/simulate/` view.

```python
result = simulate_tariff(tariff, [
    {"metric_code": "ai.input_tokens", "quantity": "1000", "unit": "input_token"},
    {"metric_code": "storage.mb_hour", "quantity": "80", "unit": "mb_hour"},
])
# result["totals_by_asset"] == {"AI_TOKEN": {"base_units": ..., "display": Decimal(...)}, ...}
```

---

## Posting Guarantees

| Rule | Where enforced |
|---|---|
| Payer balance never goes negative | Balance check inside `select_for_update()` + `transaction.atomic()` |
| No partial posts across multiple assets | All holdings locked and checked before the first entry is written |
| Immutable ledger entries | `LedgerEntry.save()` raises if pk is set; `LedgerTransaction.save()` raises if already posted |
| Idempotent reference | `tariff-usage-{usage_record.uuid}` is the unique transaction reference; duplicate calls return the existing transaction |
| Audit trace on ledger | `LedgerTransaction.metadata` stores tariff code, metric code, quantity, unit, and usage record UUID |
| FAILED status is persisted outside the atomic block | If the balance check raises `ValueError`, the atomic block rolls back, then the record is saved as FAILED in a separate write |

---

## URLs

| URL | Name | Description |
|---|---|---|
| `GET /tariffs/` | `tariffs:tariff_list` | List all tariffs with filters |
| `GET/POST /tariffs/new/` | `tariffs:tariff_create` | Create tariff |
| `GET /tariffs/<uuid>/` | `tariffs:tariff_detail` | Tariff detail with items and recent usage |
| `GET/POST /tariffs/<uuid>/edit/` | `tariffs:tariff_edit` | Edit tariff |
| `GET/POST /tariffs/<uuid>/items/new/` | `tariffs:tariff_item_create` | Add item to tariff |
| `GET/POST /tariffs/items/<pk>/edit/` | `tariffs:tariff_item_edit` | Edit tariff item |
| `GET/POST /tariffs/<uuid>/simulate/` | `tariffs:tariff_simulate` | Simulate usage charges |
| `GET /tariffs/usage/` | `tariffs:usage_list` | Usage records with filters |
| `GET/POST /tariffs/usage/new/` | `tariffs:usage_create` | Create usage record |
| `GET /tariffs/usage/<uuid>/` | `tariffs:usage_detail` | Usage detail with charges and ledger entries |
| `POST /tariffs/usage/<uuid>/post/` | `tariffs:usage_post` | Post a pending usage record |
| `GET /tariffs/metrics/` | `tariffs:metrics` | Aggregated metrics dashboard |
| `POST /tariffs/api/rate/` | `tariffs:api_rate` | JSON: rate usage without posting |
| `POST /tariffs/api/post/` | `tariffs:api_post` | JSON: record and post usage |
| `GET /tariffs/api/metrics/` | `tariffs:api_metrics` | JSON: summary metrics |

---

## JSON API

### Rate (dry run)
```http
POST /tariffs/api/rate/
Content-Type: application/json

{
  "tariff_code": "DEFAULT-AI-STORAGE",
  "metric_code": "ai.input_tokens",
  "quantity": "1200",
  "unit": "input_token"
}
```
Response:
```json
{
  "tariff_code": "DEFAULT-AI-STORAGE",
  "metric_code": "ai.input_tokens",
  "quantity": "1200",
  "unit": "input_token",
  "charges": [
    {"item_code": "ai.input_tokens", "asset": "AI_TOKEN", "amount_base_units": 1200000}
  ]
}
```

### Post (drain)
```http
POST /tariffs/api/post/
Content-Type: application/json

{
  "tariff_code": "DEFAULT-AI-STORAGE",
  "payer_account_code": "USR-001",
  "metric_code": "ai.input_tokens",
  "quantity": "1200",
  "unit": "input_token",
  "source_type": "conversation",
  "source_id": "abc-123"
}
```
Response (201):
```json
{
  "usage_record_uuid": "…",
  "status": "posted",
  "ledger_transaction_reference": "tariff-usage-…"
}
```

---

## Demo Data

```bash
python manage.py create_demo_tariffs
```

Creates three active tariffs:
- `DEFAULT-AI-STORAGE` — `AI_TOKEN` per input/output token, `STORAGE_TOKEN` per MB·hour
- `NEO4J-GRAPH` — `GRAPH_TOKEN` per node/relationship/second
- `COMPUTE-STANDARD` — `COMPUTE_TOKEN` per second/minute/hour

---

## Relation to Other Apps

| App | Relation |
|---|---|
| `toto.assets` | Uses `Asset`, `LedgerAccount`, `AssetHolding`, `LedgerTransaction`, `LedgerEntry`, `to_base_units`, `from_base_units` |
| `toto.bourse` | Users buy service tokens (AI_TOKEN, STORAGE_TOKEN, …) through exchange requests that credit the payer account — tariffs then drain those balances |
| `toto.claims` | A `Tariff` can be sourced from an `Entitlement` or `Agreement` via `source_type` / `source_id` |
| `toto.instruments` | A `SubscriptionContract` can use `record_and_post_usage` to implement metered billing on top of prepaid tokens |
