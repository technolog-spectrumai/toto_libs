# Plans

**A plan is a read-only object generated from a validated YAML file.** It is not
a database row and not a Python literal. `plans.yaml` beside this file is the
ladder this suite ships; `plans.example.yaml` is a commented copy to start from.

| | where | changed by |
|---|---|---|
| **The ladder** (what exists, what it costs, what it unlocks) | `plans.yaml` | editing a file, no migration |
| **Who may buy which tier** | `CommunityPlanOffer` rows | admin, or `ingress_subscriptions` |
| **Who is on which tier** | `Subscription` rows | `services.subscribe` |

The split is the design. A plan is a decision about the *product*; a
subscription is *user state*. Only the second belongs in the database.

## Why not rows

`SubscriptionPlan` was a table until 2026-09-02, seeded from a Python constant
in `ingress_subscriptions` and editable in the admin — two authoring paths for
one fact, with nothing keeping them in step. A tier renamed in the admin no
longer matched the seeder; re-running the seeder on a live host was a coin
flip. Generating the plans instead makes the file the only author, and the
admin's plan pages simply do not exist.

The shape is `toto.anastasia.families`': **one table, read twice, so an early
refusal and a late one can never disagree.** The plans page and the subscribe
endpoint read this registry, and there is no third answer anywhere.

## The file

```yaml
version: 1                    # must be exactly 1

plans:
  - key: standard             # the identity. ^[a-z][a-z0-9_-]{0,63}$
    name: Standard            # what a person reads. NEVER a lookup key.
    description: >            # optional, one paragraph, shown on the card
      The everyday tools.
    units: 200                # signed quantity per month. Not money.
    order: 20                 # card order, low first. Default 100.
    default: false            # exactly one plan in the file sets true
    features:                 # registered, non-free feature keys
      - editor
      - kanban
```

Every per-plan key is listed above; there are no others, and an unrecognised one
is an error rather than a thing quietly ignored.

**`units` is a quantity, not a price.** What a unit costs is the tariff rate
card's business (`toto.quota`), so re-pricing the platform never touches this
file. **Negative units are a stipend** — the same mechanism read backwards, the
platform paying the holder. A community discount is refused on a negative plan:
a discount reduces what you *owe*.

**`features` are `feature_key`s, and a `feature_key` is a Django `app_name`.**
`"cyprian"`, not `"documents"` — the gate resolves
`request.resolver_match.app_name` and nothing else, so a mapping table between
two naming schemes would be one more thing to keep in step. An app declares its
own by shipping `<app>/entitlements.py`; the suite's own are in `catalogue.py`.

**Free features are never listed in a plan.** They are unlocked for everybody by
`free=True` in the catalogue, and listing one is an error — it would read as
though the tier were selling it.

## Every validation rule

`plans.validate()` returns one string per fault, and returns *all* of them
rather than the first. Errors, in the order they are reported:

| Fault | Message |
|---|---|
| unknown top-level key | `unknown top-level key(s): [...]` |
| wrong or missing version | `version must be 1, got ...` |
| `plans` absent, empty, or not a list | `plans must be a non-empty list` |
| a plan that is not a mapping | `plans[N] must be a mapping, not str` |
| unrecognised per-plan key | `plans[N]: unknown key(s) [...]` |
| malformed or missing key | `plans[N]: key ... must match ^[a-z]...` |
| **duplicate `plan_key`** | `plans[N]: duplicate plan_key '...'` |
| missing name | `plans[N]: name is required` |
| `units`/`order` not whole numbers | `plans[N]: units must be a whole number` |
| `default` not a boolean | `plans[N]: default must be true or false` |
| a default plan that costs units | `plans[N]: the default plan cannot cost units` |
| `features` not a list | `plans[N]: features must be a list` |
| the same feature listed twice | `plans[N]: features lists the same key twice` |
| **unknown `feature_key`** | `plans[N]: unknown feature_key '...' — nothing registers it` |
| a free feature sold by a plan | `plans[N]: '...' is a FREE feature and cannot be sold by a plan` |
| zero or two defaults | `exactly one plan must set default: true, found [...]` |

Unreadable or unparseable files are the same kind of fault: `no plan file at
...`, `... is not valid YAML: ...`, `... must be a mapping, not list`.

### Where validation runs

Four hooks, on purpose — a bad ladder should be impossible to deploy, not merely
noticed.

1. **`SubscriptionsConfig.ready()`** loads the registry at startup. A malformed
   file raises `PlanError` and the host does not boot.
2. **`subscriptions.E001`** reports the same faults through `manage.py check`,
   so the deploy stops before the image is promoted. **`subscriptions.W001`**
   warns about a feature declared *paid* that no plan grants — its dashboard
   tile is hidden from everybody, superusers included, and its writes 402
   wherever enforcement is on. That has happened twice.
3. **`test_monorepo.py::PlanRegistryTests`** parses the YAML with no Django at
   all, so a file that cannot load is caught even when the settings module is
   the thing that is broken.
4. **`tests_plans.py`** exercises each rule above against a temporary file.

There is deliberately **no** check for "a plan grants something this host does
not mount". One was written and removed the same day: it fired seventeen times
on an ordinary lean build, because a ladder describes the *product* while a
build is a subset of it. The card already filters those rows out through
`registry.installed()`.

## Shipping a different ladder

```python
SUBSCRIPTION_PLANS_FILE = "/etc/toto/plans.yaml"
```

That **replaces** `plans.yaml` — it never merges. Merging would re-open the
duplicate-key question at runtime and make "what does this host sell" a query
with two answers. Point it at a copy of `plans.example.yaml`.

## Eligibility

**A plan is offered to nobody until a community offers it.** `CommunityPlanOffer`
is one row per (community, `plan_key`); with no row, nobody may subscribe.

A person is eligible for a plan when **they hold an active membership in at
least one community that offers it**. "Active membership" is the existence of a
row in `people.Person.communities` — there is no status field anywhere in the
suite, and the model docstring says so rather than inventing one.

Three exceptions, each narrow:

* **Staff** are eligible for everything, so an administrator can place someone
  on a tier no community offers yet.
* **The default plan is always eligible** for a signed-in person. It is what
  `plan_for()` puts them on, and a page that denied the plan you are already on
  would be incoherent.
* **Anonymous visitors** see the whole ladder, rendered inert. What a platform
  charges is not a secret; there is simply nothing to press.

The check is `services.is_eligible(user, plan_key)`, and it is asked **twice**:
`views.subscribe` 404s an ineligible key, and `services.subscribe` re-checks, so
calling the URL or the API directly is refused the same way.

`force=True` is the **only** bypass. No shipped caller passes it — the one
production call site is `views.subscribe`, which passes neither it nor
`approved_by` — so it is unreachable from a request and exists for an operator
at a shell, plus the tests that stand in for one. In particular `approved_by` is
**not** a bypass: it names who is accountable for a recurring outbound payment
and says nothing about whether somebody may hold a tier. Seeding a stipend on a
plan no community offers takes both arguments, deliberately.

`CommunityPlanOffer` stores a `plan_key` string, not a foreign key — which is
what lets the ladder change without a migration, and what makes a stale row
possible. A row naming a plan that left the file simply matches nothing; the
admin draws its choices from the registry, so a new one cannot be created.

## Changing the ladder on a live host

Adding, renaming, re-pricing or retiring a tier needs **no migration** — edit
the file, run `manage.py check`, deploy.

Retiring a tier leaves the people on it holding a `plan_key` that resolves to
nothing. That is handled where it belongs: `plans.get()` answers `None` and
`plan_for()` falls back to the default plan, so they lose the paid rooms rather
than meeting a crash, and their existing charge rows keep the name they paid
under. Offer them the replacement tier before you delete the old one.

**Renaming a `key` is a retirement plus a new tier.** Rename `name` instead —
that is what it is for.

## Migrating from the table

There is no data-preserving migration. The four pre-2026-09-02 migrations were
deleted and one `0001_initial` generated, so a host that ran the old ones must
drop the subscriptions tables and their `django_migrations` rows before
migrating. `zenobia/scripts/reset_subscriptions.py` does exactly that, dry-run
by default.

**The money is not in these tables.** Subscriptions, charges, discounts and
audiences are destroyed; the journals in `toto.quota` and the balances in
`toto.assets` are untouched. After migrating, run
`manage.py ingress_subscriptions` to offer the default plan to every community,
and re-create the paid offers.
