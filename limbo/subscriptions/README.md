# toto.subscriptions

Recurring plans, entitlements, allowances, usage tracking, invoices, and ledger-backed subscription payments for toto.

## Design

`subscriptions` is a domain app. It does not mutate balances directly. Payment services create invoices and delegate settlement to `toto.assets.backend.get_backend().transfer_asset(...)`, preserving the immutable ledger pattern used by the platform.

## Main models

- `SubscriptionPlan` — community-scoped product plan, e.g. AI Starter, Storage 100GB, Academy Pass.
- `SubscriptionPrice` — recurring or one-off price in an `assets.Asset`.
- `SubscriptionFeature` — entitlement or metered allowance included in a plan.
- `SubscriptionCustomer` — a `people.Person` with a billing `assets.LedgerAccount`.
- `Subscription` — active contract between a customer and plan price.
- `SubscriptionEntitlement` — granted access state.
- `SubscriptionAllowance` — current-period usage bucket.
- `SubscriptionUsage` — usage event against a feature.
- `SubscriptionInvoice` / `SubscriptionInvoiceLine` / `SubscriptionPayment` — billing and ledger settlement.
- `SubscriptionEvent` — append-only audit trail.

## Install

1. Copy `toto/subscriptions` into the repo.
2. Add `toto.subscriptions` to `INSTALLED_APPS`.
3. Include URLs, for example:

```python
path("subscriptions/", include("toto.subscriptions.urls")),
```

4. Run:

```bash
python manage.py makemigrations subscriptions
python manage.py migrate
```

The included migration assumes standard app initial migration names. If your repo has different migration dependency names, regenerate migrations locally.

## Seed examples

```bash
python manage.py ingress_subscriptions --community default --asset ASSARI
```

This creates AI Starter, Storage 100GB, and Academy Pass plans, each with a monthly price and feature allowance.

## Services

- `get_or_create_customer(...)`
- `create_subscription(...)`
- `grant_entitlements(...)`
- `reset_allowances(...)`
- `record_usage(...)`
- `create_invoice(...)`
- `pay_invoice(...)`
- `renew_subscription(...)`
- `renew_due_subscriptions(...)`
- `cancel_subscription(...)`

## Notes

- Use `Decimal`, never floats.
- Usage allowances can be hard-limited or soft-limited.
- Overage pricing can be added by creating invoice lines from `SubscriptionUsage` rows marked `over_limit`.
- The app is deliberately generic: AI, storage, academy, support, and bundle plans all use the same model layer.
