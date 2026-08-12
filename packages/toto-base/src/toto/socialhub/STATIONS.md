# Stations — the platform's offices

A **Station** is a persistent institutional position: an office. It exists
whether or not anyone holds it, holders change, and the office does not.

This is not a new idea in this codebase. The constitution seeded by
`ingress_socialhub` has promised it since it was written —

> *"Executive decisions may be delegated to appointed **Magistrates** within the
> bounds set by the Assembly."*

— and `people/civic.py::is_committed_citizen` has documented "eligibility for
non-enforcement **public roles**" for just as long. Neither was implemented. The
community org chart even emits `"title": ""` with the comment *"you have no
title field"*. A Station is that missing office.

## What an office carries

| | |
|---|---|
| `name`, `slug`, `charter` | what the office is and what it is for — **public** |
| `holder` | one Person, or empty (vacant). The office survives a vacancy. |
| `serves` | which community it works for, if any — **attribution only** |
| `since` | when the current holder took it |
| four `may_*` booleans | the same rights a community can grant — **admin-only** |
| `limit_multiplier` | headroom — **admin-only** |
| `stipend` | pay per period, from the federal treasury — **admin-only** |

## Every office is federal

`serves` records who an office works *for*. It never records who pays, because
the answer is always the same: the federal treasury (`platform-usage-fees`),
the account every tariff and every levy credits.

That is deliberate. **A community that paid its own officers would be a
community with its own budget, its own payroll and its own loyalty**, and the
design does not encourage separatism. A community that wants an office funded
asks the federation, informally — that request is a conversation, not a model,
so there is no request table, no approval workflow and no state machine. An
admin who agrees creates the row.

Communities hold no wallet at all and need none: the *user* is billed for
platform use, never the community.

## Extra limits, same taxes

`limit_multiplier` multiplies the holder's resolved quota limit, at the one
place limits are resolved (`quota/api.py::effective_limit`, read by
`check_quota`, `remaining` and `usage_summary`). The Archivist gets 10× the
upload cap because the job needs it.

**Prices and the head tax are untouched.** An office is trusted with more room,
never with cheaper money, and a test asserts the holder's charge for the same
action is byte-identical to a commoner's. A multiplier below 1 is ignored: an
office adds headroom and can never take it away.

## Who may hold one

A **committed citizen** — somebody who has signed a community constitution.
That is the promise `is_committed_citizen` already made, and `Station.clean()`
is where it finally means something. A paid office additionally needs a holder
with a login: `Person.user` is nullable and nothing creates a Person on signup,
so a user-less Person is an ordinary row here — and there is no account to pay.

## Appointment

**Django admin, and nowhere else.** Appointing is setting `holder`; vacating is
clearing it; rotation is one edit. The shape is copied from
`portfolio.BoardSeat` (an `appointed_at`, a decision record, no election).

There is deliberately no election. `portfolio`'s vote machinery is
company-scoped, zenobia-only and **explicitly non-binding** (*"nothing is
enacted — voting is not binding on the platform"*), so reusing it would mean
generalising the decision chain, the tally and both permission predicates to
buy a ceremony that decides nothing. If elections are ever wanted, they elect a
holder into this model rather than replacing it.

## Visibility

The **roster is public** (`/socialhub/stations/`, no login): which offices
exist, what each is for, who holds it, and which are vacant. An institution
nobody can see is not an institution — the roster is what makes an office
something you can be held to.

What it **never renders**: the capability booleans, the limit multiplier and
the stipend. Those are admin-only, exactly as `CommunityPrivilege` is, and a
test asserts none of them reach the page. Every row states *"Paid by the
federal treasury"*, including the ones serving a community — that is the fact a
reader is most likely to get wrong.

## The pay

`tax/payroll.py` — weekly, calendar-aligned, hung off the daily levy beat so
the platform has one clock. Gated `STIPEND_PAYOUT`, **off by default**:
collecting nothing is safe, paying out is a decision.

- Idempotent twice over: `StipendPayment` is unique per (station, period), and
  `LedgerTransaction.reference` is unique in the database — a double-fired beat
  returns the original transaction rather than paying twice.
- Paid into the holder's **billing account** — the same account a charge is
  taken from, not the prepaid account, so pay and bills meet in one place.
- `payer_account` and `payee_account` are stored on the row, not inferred: one
  row answers who paid whom, how much, when, and whether it landed.
- **A salary that cannot be paid is still owed.** This is the deliberate
  inverse of a levy: a levy that cannot collect is written off and nothing is
  owed to the platform, but an office that did its work is owed, so an unfunded
  payment stays PENDING and the next run pays it. Replay runs first, before the
  new period is materialised.

## What is deliberately not a Station

`Community.head` and `senior_members` stay exactly as they are — informal,
unpaid, and not Stations. Converting them would change the datalink wire format
(`head` is a replicated field with tests pinned to its name and a documented
role in preventing a Person↔Community cycle), the public JSON contract, the
chain-graph edge types and aurelian's mobilization tests, with nothing to
backfill `since` from. That is its own change, with its own test plan.

Also not offices, because they are assignments, qualifications or property:
`response.DeploymentAssignment`, `mobilization.Responder`,
`kanban.Practitioner`/`ProjectCommitment`, `events.organizers`,
`forum.ForumMember`, `portfolio.ShareHolding` and `portfolio.BoardSeat`.

## Federation

**Refused by datalink**, like `CommunityPrivilege` and `auth.Group` before it.
A station is authorisation *and* a salary this host's treasury pays: replicating
one would let a peer appoint an officer here and have us pay them. Each host
appoints its own, in its own admin.

## See also

- `PRIVILEGES.md` — the other shape of grant, and the head tax that pays for
  these offices.
- `zenobia/gas.md` — what is metered, what it costs, and how you get gas.
