# toto-business

**The Business Center.** Four Django apps that ship as one wheel because they
are one feature: **`toto.company`** — companies, memberships, departments and
an event-sourced share register; **`toto.voting`** — frozen rolls,
propositions, ballots and tallies, deliberately blind to what is being voted
on; **`toto.ledger`** — a generic append-only chain (blocks, canonical XML,
hashes, checkpoints) with database-level immutability triggers; and
**`toto.documents`** — export HTML builders with no renderer of their own.

`company` sits on top of the other three; `documents` and `ledger` call each
other only lazily, which is why they must share a wheel. `voting` is a pure
leaf. The chain of custody: a company decision is voted through `voting`,
committed to `ledger`, and exported through `documents`.

Two integrations stay SOFT (lazy imports behind `apps.is_installed`, the
`toto_libs/limbo/hesperis/integration/ledger.py` pattern): PDF rendering goes through
**`toto.aralia`** where a host carries it, and `ingress_company --full`
seeds a spreadsheet only where **`toto.primula`** is present. A host without
either installs and runs this wheel fine; exports refuse politely instead.

Moved host → wheel in 1.50 from `zenobia/zenobia/toto/` with app labels
preserved (every migration was reset to a fresh initial on 2026-10-01). The ledger's
immutability triggers hardcode the table name `ledger_ledgerentry`; the app
label must therefore stay `ledger` forever.
