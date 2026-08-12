# toto-economy

A double-entry ledger and the metered billing that rides on it.

| app | what it is |
|---|---|
| `toto.assets` | the ledger: assets, accounts, holdings, transactions, entries; prepaid accounts and the starting grant |
| `toto.tariffs` | the rate card: billing metrics, units, tariffs, priced items, usage records and charges |

Install both or neither. `tariffs` has six foreign keys into `assets` and its
initial migration depends on `assets.0001`; charging *is* writing ledger rows.

## Why it is a package

It was carried by one host until a second one needed a ledger of its own. A host
that pins this keeps **its own** ledger, its own prices and its own wallets —
those stay local — but the money itself does not belong to it.

**One issuer, many ledgers.** Zenobia is the only monetary master: it is the
only place a currency is created, and the only place assets are traded. Every
currency it engraves carries a permanent genesis hash that identifies it on
every platform — never a ticker, never a name, never a row id. A branch mirrors
that catalogue read-only, bills in exactly one currency Zenobia assigned it, and
cannot engrave, mint, burn, re-identify or trade anything.

**Four verbs, kept strictly apart** (`toto.mint`, master only):

| verb | what it does | supply |
|---|---|---|
| **ENGRAVE** | create a currency identity and its permanent maximum | unchanged (zero) |
| **MINT** | create units of an engraved currency, into its reserve | **+** |
| **DISTRIBUTE** | move units that already exist — an ordinary transfer | unchanged |
| **BURN** | destroy units held in the reserve | **−** |

The genesis hash commits the **maximum**, never the amount outstanding. What
exists is `Σ minted − Σ burned` over an append-only chain of signed monetary
events, computed on demand — there is no supply column anywhere, because two
places to look would be two answers that can disagree. The chain is single
-headed by a unique constraint on `prev_hash`, so a fork is structurally
impossible rather than merely unlikely.

A maximum can never be raised. Needing more than the ceiling means engraving a
new currency, because a promise you can raise is not one. And because contracts
name a currency by hash and never by supply, minting again disturbs no branch:
no new contract, no tariff pass, no conversion, no migration.

This reverses the older rule that each host should name its own gas. That rule
existed because two ledgers that could not exchange must not use one name for
two different things; the fix now is one issuer rather than separate names.

`assets.services.get_exchange_rate()` still refuses every cross-asset pair, so
an implicit FX rate can never appear inside a billing path — and on a branch the
exchange services refuse outright, because trading lives on the master.

**The full doctrine is `portal/hierarchical_economy.md`.** Read it before
changing anything about currency identity, issuance or custody.

## What it is not

**Not a dependency of metered apps.** They import `toto.quota.charge` and
nothing else. That module asks the app registry rather than importing this
package, and every function is a no-op when there is no rate card — so a host
with no economy still runs every metered endpoint, for free. Keeping that true
is what lets this package be optional.

**Not the OTC desk.** `toto.bourse` trades assets between people and stays a
host-owned portion of whichever host wants one.

## Seeding

```bash
manage.py ingress_assets          # the host's assets and their supply
manage.py ingress_tariffs         # the rate card, priced in the host's gas asset
manage.py grant_starting_gas      # backfill users who predate the grant
```

`ingress_tariffs` asserts that every priced metric is registered by an installed
app — a price nothing meters can never be charged, so pricing one fails the seed
rather than shipping as fiction. The corollary matters when apps move between
hosts: **a host may only price what it meters.**

Sizing is still a real decision, but it is no longer irreversible. The starting
grant and the prices should be sized against each other and against the expected
user base — and if the seed proves too small, the master can MINT again from the
same identity up to the engraved maximum, with no change on any branch. What
cannot be undone is the maximum itself.
