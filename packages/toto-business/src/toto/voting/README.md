# toto.voting — attributable voting

Frozen electorates, propositions, ballots, tallies. Never secret, and it knows
nothing about companies.

## The roll is a copy, not a link

Irena's voting had foreign keys into `company.Party` and read weights straight
off `company.ShareHolding`. That is why it could only ever be *one company's*
voting app.

Here a `RollEntry` carries a **reference, a name and a weight** — and this app
never resolves the reference. Who is eligible is decided outside, frozen in when
voting opens, and never looked up again.

Two things follow. Shareholders, company members and hand-picked people can all
be electorates, because they are all just rows by the time they arrive. And a
roll keeps meaning what it meant: a shareholder who sells up a month after the
vote does not retroactively change who was entitled to vote, which is the same
reason a ledger block freezes its payload. `test_the_roll_is_a_COPY_and_does_
not_move_with_the_register` is that guarantee.

## A ballot can be changed, on the record

Irena refused a second ballot. Here the earlier one is **superseded and kept**:
every ballot ever cast stays readable, exactly one is active per voter and
proposition, and only the active one counts.

`bc_one_active_ballot` is a *partial* unique constraint over
`(proposition, roll_entry) WHERE active`. The `select_for_update` in
`cast_ballot` serializes two requests for the same voter; the constraint is the
backstop if that lock is ever lost.

`Ballot.save()` refuses every write with exactly one exception —
`_supersede=True`, which may only ever set `active` to False. Anything else
about a cast ballot is history.

Superseded ballots stay visible on the page and in the PDF, greyed. A record
showing only the final vote would be concealing that somebody changed their
mind, which is itself part of the record.

## Casting requires a fresh confirmation

Not a session age — an act. `cast_ballot` refuses without a `confirmed_at`
within `CONFIRMATION_MAX_AGE_SECONDS`, and stores the evidence (method, user,
IP, user agent) on the ballot. A confirmation timestamped in the future is
refused too: a clock nobody controls is not evidence.

## Finalization takes a callback, and that is the whole design

The brief: the result must be frozen and appended to the company's ledger in
**one transaction**, and a failed append must leave the vote unfinalized.

This app must not import `toto.ledger` or `toto.company`, so it cannot append.
Instead `finalize()` runs inside `transaction.atomic` and calls
`on_finalize(proposition, payload)` **before returning**. If that callback
raises, the frozen result and the status change roll back with it.

`company/integration/voting.py` supplies an `on_finalize` that appends to the
company's chain. The caller supplies the append; the atomicity lives here.

## Membership is the qualification. Staff are not exempt.

A superuser who is not a member of the company cannot open or finalize its
votes. That is the rule the platform settled on when citizenship was retired:
belonging entitles you, not a flag on your account. An administrator can add
themselves as a member first — a visible act rather than an invisible privilege.

Casting is bound tighter still: a member may not cast somebody else's ballot
just by being a member. Only the named voter, or somebody with a recorded
representation, may use a roll entry.

## The rules, and why the tests walk a matrix

Quorum on weight or on heads; a majority measured against ballots cast /
decisive votes / present weight / all eligible weight; abstentions included,
excluded or counted as against; a threshold that must be exceeded or may be met.
None of those are hypothetical — they are what different articles actually say —
so `tests/test_tally.py` walks the combinations rather than a happy path.

The interesting cases are the ones where two readings disagree: one holder with
most of the weight is *not* most of the room, and a 50/50 tie is adopted under
`inclusive` and rejected under `strict`.

Configurations are **frozen onto** each meeting and again onto each proposition.
Editing a configuration afterwards cannot move a vote that is already open;
there is a test that says so.

## Guards read the database, not the instance

`Proposition.delete()` and `RollEntry.delete()` re-read the current status
rather than trusting the object in hand. A guard that trusted an in-memory value
would let anybody holding an object fetched before the vote opened delete it
afterwards — and objects are held across requests all the time. That was a real
hole, caught by `test_a_proposition_in_voting_cannot_be_deleted`.

## Running the tests

```bash
BUILD_BUSINESS_CENTER=1 python manage.py test \
    toto.voting.tests.test_tally toto.voting.tests.test_lifecycle
# and the company-bound half:
BUILD_BUSINESS_CENTER=1 python manage.py test \
    toto.company.tests.test_voting_integration \
    toto.company.tests.test_vote_views
```
