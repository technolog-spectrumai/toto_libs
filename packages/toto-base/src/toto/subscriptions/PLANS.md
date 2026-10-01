# Plans

**A plan is a read-only object generated from a validated YAML file.** It is not
a database row and not a Python literal. `plans.yaml` beside this file is the
ladder this suite ships; `plans.example.yaml` is a commented copy to start from.

| | where | changed by |
|---|---|---|
| **The ladder** (what exists, what it costs, what it unlocks) | `plans.yaml` | editing a file, no migration |
| **Who may buy which tier** | `CommunityPlanOffer` rows | admin, or `ingress_subscriptions` |
| **Who is on which tier** | `Subscription` rows | `services.subscribe` |
| **Whose row is on a plan for admins** | `Subscription.for_admins` | the plan, on every save — never a form |

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
    for_admins: false         # only Django superusers see, take or hold it
                              # (admin_only: the older spelling, same flag)
    all_features: false       # 1.51: grants every feature without listing them
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

The same discount reaches every **mana** charge too (2026-09-28): a member who
pays 20 % less for their plan pays 20 % less for a message, a scan or a run
(`toto.tariffs.discounts`; the economy's decision 15). The plan's own bill is
never discounted twice.

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
| `default`, `for_admins`, `admin_only` or `all_features` not a boolean | `plans[N]: default must be true or false` |
| **`for_admins` and `admin_only` both set, and different** | `plans[N]: for_admins and admin_only are one flag and disagree — write for_admins only` |
| a default plan for admins | `plans[N]: the default plan cannot be for_admins (admin_only)` |
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

**Communities only** (2026-09-28). A clearance (`socialhub.Clearance` —
`internal`, `confidential`, its own model since 2026-09-29) decides who reads
and how fast its holders' mana refills, and nothing on this axis: an offer
and a discount name a `Community`, and a clearance is not one, so neither can
be given to it. Plans do not set refill speed either — clearances do. The two
are orthogonal on purpose — `socialhub/README.md`, "Communities and
clearances".

Four exceptions, each narrow:

* **A plan for admins** (`for_admins: true`) is outside the offer rule
  altogether: every Django superuser is eligible for it and nobody else ever
  is — staff included, whatever a Community offers (2026-09-28).
* **Staff** are eligible for everything else, so an administrator can place
  someone on a tier no community offers yet.
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

There is no migration path: every database was rebuilt from scratch at the
2026-10-01 migrations reset, when every app's history was replaced by a fresh
`0001_initial`. `zenobia/scripts/reset_subscriptions.py`, which served the
databases from before 2026-09-02, is therefore obsolete.


## Plans for admins and all-features plans (1.51; 2026-09-26; 2026-09-28)

`for_admins: true` makes a plan for administrators — only Django superusers
can see it, subscribe to it or hold it. `admin_only: true` is the older
spelling of the same flag and is still read; a plan that states both must
state the same value, or the ladder is refused. In Python the flag is
`Plan.admin_only`, with `Plan.for_admins` as its readable alias.

**Who may take it: every superuser, and nobody else** (2026-09-28). A
superuser needs no Community offer — `services.is_eligible` asks for the
account alone — so a superuser in no Community at all sees the card on the
plans page, marked *For administrators*, and its button works. Staff and
members never see the card (`services.eligible_plans` leaves it out even
where a Community of theirs offers the plan) and get a 404 from the subscribe
door; `subscribe(force=True)` refuses them too. It cannot be the default plan.

**The row says so.** `Subscription.for_admins` is set from the plan on every
save (a switch down to Developer clears it, a switch back sets it) and is
editable nowhere; the admin lists and filters by it and shows it read-only.
The model refuses a live row (active or in arrears) on a plan for admins for
an account that is not a superuser — `Subscription.clean()` raises a
`ValidationError` and `save()` refuses the same way — so neither the admin
nor a script can bypass `services.subscribe`. Writing such a row down as
lapsed or cancelled is always allowed, the admin screen included: its form
checks a plan only for a live row, so the row of an admin who was stepped
down can be cancelled or lapsed there. `Subscription.save()` sets
`for_admins` from the plan, so no migration backfills it (the old `0003` that
marked existing rows went with the 2026-10-01 reset). After a ladder edit
(a plan gains or loses the flag, the admin plan is renamed) the stored flag
is re-read for every row by `services.sync_for_admins()`, which
`bootstrap_plans` (so every deploy) and the billing sweep run; nothing
enforces from the stored flag, the plan is always what decides.

**It is not granted by the privilege alone**: a superuser holds it through
their own subscription. `bootstrap_plans` puts every superuser on it in one
go (it also makes the `operators` Community and offers every plan to it), and
every door that creates accounts (`bootstrap_users`, so the build scripts,
`deploy.py users` and the Operator's Users tab) runs it. Nor does the plan
grant anything by itself: `plan_for` ignores it on an account that is not a
superuser — a holder who is demoted is on the default plan on the next
request — and the billing sweep lapses such a row with the reason
`not-superuser` (a failed or cleared month on it lapses it the same way
rather than keeping it live). Superuser functionality — `visibility:
superuser` tiles, views wearing `gate.superuser_plan_required` — asks
`models.superuser_plan_active(user)`: **both**, the account and the plan.

## Community-defined and checked live (2026-09-26)

Plans are personal; what a person may hold is what a Community they belong to
offers (`CommunityPlanOffer`), and offers **inherit down the tree**: a member
of `toto-dev` (`parent=toto`) may choose what `toto` offers plus what
`toto-dev` adds — a child adds, never removes. The check is not made once at
purchase but on every `plan_for`: leaving the Community, an offer withdrawn, a
`Subscription.expires_at` in the past or a key that left the file all resolve
to the default plan on the next request, and the daily `run_billing` lapses
the row with the reason (`withdrawn`, `expired`, `unknown-plan`,
`not-superuser`, `overdue`) so the Current plan page can say why. Two
exceptions, both deliberate: staff and superusers may hold any buyable plan
without an offer (they administer the ladder), and an **operator's grant**
(`subscribe(force=True)`, recorded as `Subscription.forced`) needs no offer —
it was given, not bought — though it still expires and still refuses a plan
for admins to a non-superuser. Since 2026-09-28 a plan for admins needs no
offer at all (above). Only real superusers change offers,
discounts or subscription rows: the two tabs answer 403 to staff, and the
admin section is superusers' alone.

`all_features: true` grants every feature, present and future, without
listing them. Together they make a "Superuser" tier. A ladder whose only
all-features plan is for admins still gets `subscriptions.W001` for any paid
feature no buyable plan lists.

## Enforcement switches (1.51)

`SubscriptionGateMiddleware` reads two settings at request time:

| Setting | Default | Effect |
|---|---|---|
| `SUBSCRIPTION_ENFORCEMENT` | `True` | `False` lets every request through while the middleware stays installed — for a host that makes enforcement obligatory in deployments but runs its app test suites without it. |
| `SUBSCRIPTION_GATE_READS` | `False` | `True` refuses safe methods too: an app outside the plan is not opened at all (402, the locked page), instead of read-only. |
