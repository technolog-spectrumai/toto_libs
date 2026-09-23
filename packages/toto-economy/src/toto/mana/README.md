# toto.mana — three pools over the economy

A member sees **security** (accent), **compute** (`warn`, red) and **storage**
(`success`, green) mana: pools that drop when they act, refill every hour up to
a cap, and — for security — rise when they encrypt. Staff keep the full
economy. The doctrine, and how this fits it, is `/economy.md#mana`; the
campaign checklist is `/todo.md`.

## What lives where

| File | What it is |
|---|---|
| `colours.py` | Pure data: roles, hues, tickers, the metric → role map, seed prices, dials. The only place a metric gets a colour. |
| `status.py` | Pure rules: fill, band (`empty`/`low`/`ok`), trend, ETA, the encrypt pre-selection. |
| `models.py` | `ManaPool` (role → asset, PROTECT; the two dials) and `ManaGrant` (the idempotency claim). No balances. |
| `bootstrap.py` | `ensure_mana`: retire MANA, mint BLUE/RED/GREEN once, bind the pools once. Runs inside `bootstrap_economy`, i.e. before every ingress. |
| `services.py` | Everything else: bindings, prices, refill (`top_up`, `regenerate_hour`, `fill_pools`), the encrypt reward, the read side (`balances_of`, `history`, `series`, `plain_files`) and the refusal sentence. |
| `management/commands/ingress_mana.py` | Seed/repair prices, clamp-then-arm the two levies, fill members who predate the pools. |
| `tasks.py` | The hourly refill, from the clock. |
| `views.py`, `urls.py`, `templates/mana/` | L1 `/mana/`, L2 `/mana/<colour>/`, L3 `/mana/about/`, and `api/balances/` for the chip. |
| `plugins/` | The header chip (L0) and the own-profile section. |

Outside this directory, the pieces it relies on: `HeaderPlugin` (toto.core),
`file_encrypted` and `PlaintextLevy` (toto.vault), `TaxRule.clamp_to_balance`
(toto.tax), `InsufficientBalanceError.detail` (toto.tariffs), and zenobia's
`StaffPrefixMiddleware`.

## Rules that must not bend

- **The ledger is the balance.** Never store a pool level here; read the holding.
- **Every write is claimed first.** A `ManaGrant` row the database referees, then a transfer with a unique reference. Never pay without the claim.
- **A pool levy is clamped.** A levy priced in a pool must have `clamp_to_balance`, set *before* it is armed — or an empty pool freezes every metered write on the platform.
- **Guards key on the asset, not the code.** "Priced in a pool asset" is the question; a host with the app but no pools yet still bills in gas.
- **Every colour class is written out literally** in templates: tailwind reads the page, not the Python.

## Tests

- **Unit** — `toto.mana.tests.unit.*`: each operation called directly. Fast; run on every change, and in the clean-env gate.
- **Integration** — `toto.mana.tests.integration.*`: management commands, page renders, the tax sweep, the real encryption strategy, the staff gate, one member's whole day. Run by hand: **`./user_tests.sh`** at the repo root (`--wide` adds the economy suites mana touches).

Either way the test venv (`zenobia/.venv_test`) holds a *copy* of this wheel:
rebuild and reinstall it before a run means anything (`user_tests.sh` does).
Locally you also need what the gate exports — a `MONETARY_ISSUER_KEY` and a
writable `MEDIA_ROOT`.
