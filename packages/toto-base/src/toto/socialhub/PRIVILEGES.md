# Privileges and offices

**Rights are held by institutions, never by persons.** There are two, and they
grant in two different shapes:

| | grants to | set in | visible |
|---|---|---|---|
| **Community** (`CommunityPrivilege`) | every member | Django admin only | never |
| **Station** (an office) | its one current holder | Django admin only | the roster is public; what it grants and pays is not |

A person holds the **union** of both — highest privilege always. Membership is
invite-gated (`MembershipApplication` → accepted → `communities.add` is the only
door) and an office is appointed in admin, so admitting or appointing someone
*is* the grant, and expelling them or vacating the office *is* the revocation.
Neither can hand one named individual a right their successor will not inherit,
which is the property the whole design preserves.

`socialhub/privileges.py` is the one resolver everything asks. Every function in
it degrades to the commoner answer (no rights, no headroom) rather than
raising: a gate failing open would be escalation.

## Every field, and the gate that honours it

| Field | On | Honoured at |
|---|---|---|
| `may_see_community_chain` | both | `socialhub/views/community.py::community_chain_graph_data` |
| `may_administer_communities` | both | `AdministrataView.dispatch`, and the button on the community page |
| `may_manage_community_news` | both | `socialhub/permissions.py::can_manage_community_news` — joins head and senior members, reaching across communities |
| `may_operate_mint` | both | `mint/views.py::_staff_only` — the **user half only** (see below) |
| `limit_multiplier` | station | `quota/api.py::effective_limit`, read by `check_quota`, `remaining` and `usage_summary` |
| `stipend` | station | the payroll sweep — paid by the federal treasury |

### The mint is not delegable

`_master_only` is untouched. `is_monetary_master()` takes no user argument — it
asks whether the *machine* holds a decryptable issuer key — so on a branch host
a privileged member reaches the page and is told "not the master", exactly as a
staff member would be. The privilege admits a person to the button; it cannot
make the button work. Asserted in `tax/tests/test_privileges.py::MintHonestyTests`.

## Money

**Tax is federal.** Everyone pays the federation; the federation pays its
officers. There is one receiver of taxes and one payer of salaries, and they are
the same account (`platform-usage-fees`).

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
- **`limit_multiplier`** — headroom, never money. An office gets the room its
  work needs, and **pays exactly what anyone else pays for the same action**.
  Asserted directly on the ledger, because that is the constraint.
- **`stipend`** — paid per period by the federal treasury to the holder's
  billing account (the same account a charge debits). A vacant office pays
  nobody and accrues nothing.

### Why every office is federal

`Station.serves` records which community an office works *for*. It never records
who pays, because the answer is always the same. A community that paid its own
officers would be a community with its own budget, its own payroll and its own
loyalty — so a community that wants an office funded asks the federation
informally, and an admin who agrees creates the row. There is no request table
and no approval workflow, deliberately.

## What this generalises

- `Community.is_federal_tribe` — promised *"members are exempt from all poll
  taxes"* while no poll tax existed. One briefly did (the head tax), and
  migration `socialhub/0004` gave every flagged community `head_weight = 0`,
  the exemption it always meant. Both the tax and the weight have since been
  removed, and `0004` remains untouched because a historical migration describes
  what happened, not what is true. The boolean stays, deprecated, because
  **aurelian** reads it by name for responder eligibility.
- `Person.is_federal_agent` — help text about taxes, code about two admin views.
  Those two rights are now an **office**: the migration creates a *Federal Agent*
  station holding them, with the flagged person as holder. If several held the
  flag, the first becomes holder and the rest are named in the charter rather
  than silently dropped. The field stays for aurelian's templates.
- The constitution this platform seeds has promised *"Executive decisions may be
  delegated to appointed **Magistrates**"* since it was written, and
  `people/civic.py::is_committed_citizen` has promised "eligibility for
  non-enforcement public roles". A Station is that office, and citizenship is
  what qualifies a holder for one (`Station.clean`).

Retiring the two flags is an aurelian follow-up
(`mobilization/models.py:134`, `responder_recruit.html`, `responder_detail.html`).

## Federation

Both models are **refused by datalink**. A privilege row is authorisation, and
replicating it would let a peer's admin grant rights on this host by editing
their own database — the same reason `auth.User`/`auth.Group` are refused. A
station is authorisation *and* a salary this host's treasury pays: replicating
one would let a peer appoint an officer here and have us pay them.

## What is deliberately NOT here

- **`Community.head` / `senior_members`** stay exactly as they are: informal,
  unpaid, and not Stations. Converting them would change the datalink wire
  format (`head` is a replicated field with tests pinned to its name), the
  public JSON contract, the chain-graph edge types and aurelian's mobilization
  tests, with nothing to backfill `since` from. That is its own change.
- **No allowances anywhere.** A free per-metric band is shareable — the way to
  use it is to route work through someone whose band is unspent. The free tier
  is the *absence of a price*, and a metric with no price row is free for
  everyone.
