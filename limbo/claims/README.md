# toto.claims

Contract lifecycle primitives. The four models here are the runtime objects that Lapis smart contracts and financial instruments create and track. They attach to an `assets.Agreement` or `assets.Contract` and are the primary source of `ContractEvent` records.

## Purpose

When a financial instrument or Lapis contract executes, it doesn't just post ledger entries — it creates structured state: an `Entitlement` grants a right, a `Schedule` schedules future billings, a `Condition` gates an effect, an `Allocation` ring-fences funds. Every significant state change appends a `ContractEvent` to the audit log. This gives contracts a queryable lifecycle history — you can always answer "what happened under this agreement, and when."

## Models

- `Entitlement` — a right held by a `LedgerAccount`. Kinds: `service_access`, `lease_right`, `exercise_right`, `reward_eligibility`, `claim_right`, `usage_right`. Fields: `agreement` / `contract` FK, `holder_account`, `kind`, `status` (`active / suspended / expired / revoked`), `starts_at`, `ends_at`, `metadata`.

- `Schedule` — a temporal trigger for billing, renewal, vesting, payout, settlement, reward, or checkpoint events. Fields: `agreement` / `contract` FK, `kind`, `status` (`active / paused / completed / cancelled`), `frequency` (`once / daily / weekly / monthly / quarterly / annual / custom`), `next_run_at` (indexed), `last_run_at`, `run_count`, `max_runs` (nullable), `cron_expression` (for custom frequency).

- `Condition` — a predicate that gates an effect. Kinds: `time`, `status`, `approval`, `balance`, `evidence`, `threshold`, `manual`, `external`. Fields: `agreement` / `contract` FK, `kind`, `status` (`pending / satisfied / failed / waived`), `expression` (JSON — encodes the rule), `evaluated_at`, `evaluator` (FK to `people.Person`, for manual/approval kinds).

- `Allocation` — a ring-fenced asset reserve. Kinds: `escrow_hold`, `collateral`, `margin`, `vesting_pool`, `staking_lock`, `prepaid_balance`, `budget`. Fields: `agreement` / `contract` FK, `asset` (FK to `assets.Asset`), `account` (FK to `assets.LedgerAccount`), `kind`, `status` (`active / released / consumed / cancelled`), `amount_base_units`, `allocated_base_units`, `released_base_units`, `consumed_base_units`.

- `ContractEvent` — append-only audit log of contract lifecycle events. Fields: `agreement` / `contract` FK, `kind` (rich enum: `payment_due`, `payment_paid`, `entitlement_granted`, `condition_satisfied`, `allocation_released`, `default`, `settlement`, etc.), `transaction` (FK to `assets.LedgerTransaction`, nullable), `obligation`, `entitlement`, `schedule`, `condition`, `allocation` (all nullable FKs), `metadata`, `occurred_at`.

## Key coupling

- `instruments` — all 10 instrument types create and update `Entitlement`, `Schedule`, `Condition`, `Allocation`, and `ContractEvent` records through their service classes.
- `assets.Agreement` — the runtime instance of a contract; most claims objects FK here.
- A Celery task polls `Schedule.next_run_at` hourly to process due schedules.

## Dependencies

- `assets` — Entitlement and Allocation reference Asset and LedgerAccount
