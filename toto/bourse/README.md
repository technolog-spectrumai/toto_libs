# toto.bourse

Peer-to-peer asset exchange requests. Members offer to exchange a quantity of one asset for another at a specified rate. Matching and settlement go through the `assets` ledger.

## Models

- `AssetExchangeRequest` — an offer to exchange assets. Fields:
  - `requester` (FK to `people.Person`)
  - `offer_asset` (FK to `assets.Asset`), `offer_amount_base_units`
  - `request_asset` (FK to `assets.Asset`), `request_amount_base_units`
  - `offer_account` / `request_account` (FKs to `assets.LedgerAccount`)
  - `status` — `open / accepted / rejected / cancelled / expired`
  - `counterparty` (FK to `people.Person`, nullable — set on acceptance)
  - `response_note`
  - `expires_at`, `created_at`, `updated_at`

## Services (`services.py`)

| Function | What it does |
|---|---|
| `accept_exchange_request(exchange_request, counterparty_account, response_note)` | Validates both accounts have sufficient balance; posts ledger transactions for both sides; sets `status=accepted` |
| `reject_exchange_request(exchange_request, response_note)` | Sets `status=rejected` |
| `cancel_exchange_request(exchange_request, response_note)` | Sets `status=cancelled` (requester-initiated) |

## Key coupling

- `assets.Asset`, `assets.LedgerAccount` — exchange uses ledger accounts for both sides of the trade.
- `people.Person` — requester and counterparty.
