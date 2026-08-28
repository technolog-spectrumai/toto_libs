# toto.company — the Business Center's integration root

Companies, the people attached to them, the department tree and the share
register. One of four apps behind `BUILD_BUSINESS_CENTER`; the only one allowed
to reach into the rest of Zenobia.

## Where this code came from

Revived from the deleted **irena** host, recoverable at `goodbye_irena^`, where
it lived as `toto.company` plus `toto.departments`. The two are one app here.
Almost nothing was rewritten: `services/ownership.py` is byte-for-byte the
original but for one message that named Irena, and the department models,
the Cytoscape renderer and the ownership arithmetic are the originals.

What is new: `CompanyMembership` (irena had only department seats), the logo
and statute fields, three separate views in place of one 515-line detail page,
and the test batteries.

## The boundary, and why it is a test

The rule the whole Business Center rests on:

```
company ──uses──▶ people, events, vault, socialhub, core   (never the reverse)
company ──uses──▶ ledger, documents, voting                (never the reverse)
```

`tests/test_boundary.py` walks the AST of every module in every Business Center
app and enforces both halves — that these apps import no retired predecessor,
that `ledger`, `documents` and `voting` stay Company-blind, and that no other
host-owned app imports any of them. Irena shipped the first half of that test;
the second half is what the brief actually turns on, and it is the half that
catches the mistake irena itself made — its ledger service imported
`toto.company.models.Company` directly.

## Ownership is exact

Units are `Decimal(24, 6)` and no float touches the path. `OwnershipEvent` is
append-only: `save()` refuses an update, `delete()` refuses outright, and the
live register is a projection of those events rather than a thing edited in
place. `services/ownership.py` does every mutation inside `transaction.atomic`
with `select_for_update` on the live holding, so two concurrent transfers
cannot both read the same balance.

`ShareHolding` carries a partial unique constraint — one live holding per party
and share class — so a bug that forgot to close the previous row fails at the
database rather than double-counting.

The pie on the Shareholders page is by **votes**, not units: a share class may
carry a `votes_per_unit` other than 1, and control is what the chart is about.

## The three views

| View | Route | What it holds |
|---|---|---|
| Company Structure | `company:structure` | Identity, legal facts, statute, members, the department tree |
| Shareholders | `company:shareholders` | The register, the exact ownership pie, the event log |
| Organization Chart | `company:org_chart` | Cytoscape, plus the same hierarchy as a readable table |

The chart is never the only way to read the hierarchy: every page that draws
one also renders the tree as an indented table, which is what a screen reader,
a printout and a browser with no JavaScript get.

## Job titles carry no authority

`CompanyMembership.job_title` is plain text on purpose — no role vocabulary, no
permission meaning. Authorization reads `active` and `is_staff`, never what the
title says. A "Director" string in that column grants nothing.

## Statute

Either plain text or a protected vault file, following socialhub's
`Community.statute` precedent exactly: `SET_NULL` and never `CASCADE`, because
deleting a file must not delete the company that adopted it, and the link goes
through `vault:public_file` so the vault's own download route enforces
`may_read`. This app's templates never decide who may read a statute.

## Running the tests

`toto` is a PEP 420 namespace package, so the runner cannot discover it by
package label — name the modules:

```bash
BUILD_BUSINESS_CENTER=1 python manage.py test \
    toto.company.tests.test_boundary \
    toto.company.tests.test_ownership \
    toto.company.tests.test_structure \
    toto.company.tests.test_views
```

The flag's own contract is tested from the host side, in
`zenobia/tests/test_business_center_flag.py`: it boots a second interpreter
with the flag cleared and asserts not only that no app is installed but that no
module was even imported — because an unconditional import somewhere in the host
would leave the module in `sys.modules` while the app registry stayed clean.

## Still to come

Stages 2–4 add `toto.ledger`, `toto.documents` and `toto.voting`.
`DepartmentDecision` and its service are deliberately **not** here yet: they
write to a ledger, and they land with it in Stage 2.
