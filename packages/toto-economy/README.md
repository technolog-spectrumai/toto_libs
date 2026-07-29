# toto-economy

A double-entry ledger and the metered billing that rides on it.

| app | what it is |
|---|---|
| `toto.assets` | the ledger: assets, accounts, holdings, transactions, entries; prepaid accounts and the starting grant |
| `toto.tariffs` | the rate card: billing metrics, units, tariffs, priced items, usage records and charges |

Install both or neither. `tariffs` has six foreign keys into `assets` and its
initial migration depends on `assets.0001`; charging *is* writing ledger rows.

## Why it is a package

It was carried by one host until a second one needed to price its own work
differently. A host that pins this runs **its own** economy: its own assets, its
own prices, its own wallets. Nothing crosses between hosts — the bourse is
per-host and `assets.services.get_exchange_rate()` refuses every cross-asset
pair, so an implicit FX rate can never appear inside a billing path.

Two hosts each running this are two economies, not one shared one. That is the
point, and it is why a host billing in the same ticker as its sibling is a
mistake: two things named alike that cannot be exchanged is exactly the
ambiguity the no-conversion rule exists to prevent. Give each host's gas its own
name.

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
