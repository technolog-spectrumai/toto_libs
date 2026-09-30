# toto.mana — three pools over the economy

A member sees **security** (`security`, cyan), **compute** (`warn`, red) and **storage**
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
| `bootstrap.py` | `ensure_mana`: retire MANA, mint BLUE/RED/GREEN once (short names SECU/COMP/STOR, so their pages are `/assets/assets/SECU/` and so on), bind the pools once. Runs inside `bootstrap_economy`, i.e. before every ingress. |
| `services.py` | Everything else: bindings, prices, refill (`top_up`, `regenerate_hour`, `fill_pools`), the encrypt reward, the manual grant (`grant_manual`), the read side (`balances_of`, `history`, `series`, `plain_files`) and the refusal sentence. |
| `faucets.py` | Every increase as a faucet payout (2026-09-26): four `assets.Faucet` rows per pool — `mana-<role>-{hourly,signup,encrypt-reward,manual}` — and `record_payout`, written beside the transfer in the same transaction. |
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
- **A clearance sets the refill speed** (2026-09-28). A clearance (`socialhub.Clearance`, named after what it opens — internal, confidential) may set a speed per pool (`regen_security`, `regen_compute`, `regen_storage`, per hour); a member refills at the fastest speed any of their clearances sets for that pool, and at the pool's own `regen_per_hour` when none does — no clearance, or clearances that set nothing. A clearance may set a slower speed too, and 0 stops that member's refill. The pool's own rate at 0 is still the operator's off switch for everybody. `clearance_speeds` reads every member's clearances in one query per hourly run; `regen_for` is the one rule the run, the refusal sentence (`explain_shortfall`) and the pages (`balances_of`) share. **Regeneration** (`/mana/regeneration/`, beside Mana in the economy strip) shows a member their speed per pool and the clearances it comes from — only their own clearances, because clearances are hidden from members; a superuser sees every clearance. Communities and plans never set speed: a clearance is its own model, so they cannot.
- **Every increase is a faucet payout.** The hourly refill, the opening fill, the encrypt reward and a grant by hand each leave a `FaucetPayout` (source, recipient, amount, time, the transaction) on the pool's faucet, and the refill leaves a `FaucetRun` per pool per execution. Nothing else puts mana on an account: the asset's Distribute button refuses a pool asset and points at the manual faucet, which needs the mint right and a reason; the admin cannot type a balance. A refund of failed metered work is a reversal, labelled as such, not regeneration.
- **A pool levy is clamped.** A levy priced in a pool must have `clamp_to_balance`, set *before* it is armed — or an empty pool freezes every metered write on the platform.
- **Guards key on the asset, not the code.** "Priced in a pool asset" is the question; a host with the app but no pools yet still bills in gas.
- **Every colour class is written out literally** in templates: tailwind reads the page, not the Python.

## Tests

- **Unit** — `toto.mana.tests.unit.*`: each operation called directly. Fast; run on every change, and in the clean-env gate.
- **Integration** — `toto.mana.tests.integration.*`: management commands, page renders, the tax sweep, the real encryption strategy, the staff gate, one member's whole day. Run by hand: **`./user_tests.sh`** at the repo root (`--wide` adds the economy suites mana touches).

`zenobia/manage.py test` imports this source tree: `manage.py` puts
`vendor/toto_libs/packages/*/src` first on `sys.path` unless `TOTO_SRC` is set.
The installed *copy* in `zenobia/.venv_test` is what the clean-env gate and a
`python -m django` run from `/tmp` import, so rebuild it before those
(`user_tests.sh` does). Locally you also need what the gate exports — a
`MONETARY_ISSUER_KEY` and a writable `MEDIA_ROOT`.
