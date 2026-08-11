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
currency it issues carries a permanent genesis hash that identifies it on every
platform — never a ticker, never a name, never a row id. A branch mirrors that
catalogue read-only, bills in exactly one currency Zenobia assigned it, and
cannot issue, re-identify or trade anything.

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

An asset's supply is minted once and there is no mint path, so the starting
grant and the prices have to be sized against each other and against the whole
user base before the first seed.
