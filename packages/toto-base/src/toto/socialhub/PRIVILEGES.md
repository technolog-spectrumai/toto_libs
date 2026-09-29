# Privileges

**Rights are held by institutions, never by persons.** There is one:

| | grants to | set in | visible |
|---|---|---|---|
| **Community** (`CommunityPrivilege`) | every member | Django admin only | never |

**Highest privilege always**: any community a person belongs to that grants a
right grants it. Membership is invite-gated (`MembershipApplication` → accepted
→ `communities.add` is the only door), so admitting someone *is* the grant and
expelling them *is* the revocation. It cannot hand one named individual a right
their successor will not inherit, which is the property the whole design
preserves.

**A clearance grants nothing** (2026-09-28). A clearance (`socialhub.Clearance`,
its own model since 2026-09-29) decides who reads and is given by a superuser,
never applied for — so it must not be a way to hand out a right. A privilege
names a `Community`, and a clearance is not one, so no row can cross: the type
refuses what a flag would have had to police. Why the two never cross:
[README.md](README.md), "Communities and clearances".

## There used to be two

`Station` — a "Special Role" — was the second: an office that granted the same
four rights to whoever currently held it, carried a `limit_multiplier` that
bought that office extra quota headroom, and carried a stipend the treasury paid
every period. Three unrelated things in one row, and being paid was tangled up
with being trusted.

It was removed in 8/2026, along with `STATIONS.md`, the public roster at
`/stations/`, the treasury payroll (`tax.payroll`) that paid it, and
`privileges.limit_multiplier_for`. Quota limits are flat for everybody now, and
recurring payment is a **faucet** (`toto.assets`) — which pays people, grants
nothing and qualifies nobody.

`socialhub/privileges.py` is the one resolver everything asks. Every function in
it degrades to the commoner answer (no rights, no headroom) rather than
raising: a gate failing open would be escalation.

## Every field, and the gate that honours it

| Field | Honoured at |
|---|---|
| `may_see_community_chain` | `socialhub/views/community.py::community_chain_graph_data` |
| `may_administer_communities` | `AdministrataView.dispatch`, and the button on the community page |
| `may_manage_community_news` | `socialhub/permissions.py::can_manage_community_news` — joins head and senior members, reaching across communities |
| `may_operate_mint` | `mint/views.py::_staff_only` — the **user half only** (see below) |

`limit_multiplier` and `stipend` were on this table until 8/2026. Both were
Station's, and both went with it: quota limits are flat for everybody, and
paying somebody is a faucet.

### The mint is not delegable

`_master_only` is untouched. `is_monetary_master()` takes no user argument — it
asks whether the *machine* holds a decryptable issuer key — so on a branch host
a privileged member reaches the page and is told "not the master", exactly as a
staff member would be. The privilege admits a person to the button; it cannot
make the button work. Asserted in `tax/tests/test_privileges.py::MintHonestyTests`.

## Money

**Tax is federal.** Everyone pays the federation. There is one receiver of
taxes (`platform-usage-fees`), and until 8/2026 it was also the payer of
salaries — the treasury paid the platform's offices out of what it collected.
Offices are gone; what puts value back now is a **faucet**
(`toto.assets.services.faucets`), which pays named people by the hour out of an
asset's reserve rather than out of the fee account.

- **A community never receives anything, and this file no longer sets what its
  members owe.** It has no wallet, no treasurer and no payroll, and needs none —
  the *user* is billed for platform use, never the community. What a community
  does to a member's bill is a **discount on a subscription plan**
  (`toto.subscriptions.CommunityPlanDiscount`), and the highest discount across
  a person's communities wins. That replaced `head_weight`, which said the same
  thing in the opposite direction (lowest weight won) for a head tax that no
  longer exists.
- **Concentration is charged on top, and no concession shelters it.** A levy
  rule carries `concentration_k`; each holder owes `k × share²` in addition,
  where share is their holdings over what all users hold between them. It is
  **added** to what the provider measured rather than multiplying it, so a
  concession on a resource never becomes immunity from this — a Gini regulator
  you could escape would not be one. Default `k = 0` is off.
- **Nothing here buys headroom or pays anybody.** `limit_multiplier` and
  `stipend` were Station's and went with it. A privilege moves a *gate* and
  only a gate: a person who holds one **pays exactly what anyone else pays for
  the same action**, which is asserted directly on the ledger in
  `tax/tests/test_privileges.py`, because that is the constraint.

### Being paid is not being trusted

That separation is the point of removing Station. It fused an office, an
authorisation grant and a payslip into one row, so being paid meant holding a
post and holding a post meant holding rights. A **faucet** pays people and
grants nothing; a **community privilege** grants rights and pays nothing. Either
can be given without the other, which was never true before.

## What this generalises

- `Community.is_federal_tribe` — promised *"members are exempt from all poll
  taxes"* while no poll tax existed. One briefly did (the head tax), and
  migration `socialhub/0004` gave every flagged community `head_weight = 0`,
  the exemption it always meant. Both the tax and the weight have since been
  removed, and `0004` remains untouched because a historical migration describes
  what happened, not what is true. The boolean stays, deprecated, because
  **aurelian** reads it by name for responder eligibility.
- `Person.is_federal_agent` — help text about taxes, code about two admin views.
  Migration `socialhub/0004` turned those two rights into a *Federal Agent*
  **office**, with the flagged person as holder. Offices were removed in 8/2026,
  so what that migration created no longer exists — and `0004` remains untouched
  for the same reason as above: a historical migration describes what happened,
  not what is true. The field stays for aurelian's templates.

Retiring the two flags is an aurelian follow-up
(`mobilization/models.py:134`, `responder_recruit.html`, `responder_detail.html`).

## Federation

`CommunityPrivilege` is **refused by datalink**. A privilege row is
authorisation, and replicating it would let a peer's admin grant rights on this
host by editing their own database — the same reason `auth.User`/`auth.Group`
are refused.

`Station` was refused for that reason *and* one more: it carried a salary this
host's treasury paid, so replicating one would have let a peer appoint an
officer here and have us pay them. The successor inherits that second reason —
`assets.Faucet` and `assets.FaucetMember` must never be replicable, or a peer
could add themselves to a faucet on this host and be paid hourly out of our
reserve.

## What is deliberately NOT here

- **`Community.head` / `senior_members`** stay exactly as they are: informal
  and unpaid. They were never offices, and with offices gone there is nothing
  left to convert them into — but the reasons not to touch them stand on their
  own: it would change the datalink wire format (`head` is a replicated field
  with tests pinned to its name), the public JSON contract, the chain-graph edge
  types and aurelian's mobilization tests. That is its own change.
- **No allowances anywhere.** A free per-metric band is shareable — the way to
  use it is to route work through someone whose band is unspent. The free tier
  is the *absence of a price*, and a metric with no price row is free for
  everyone.
