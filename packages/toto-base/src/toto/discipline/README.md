# Discipline: one mana, and what regenerates it

A layer between the platform and whatever economy stands behind it
(2026-10-11). The platform's features know one thing: an action costs mana,
and a person has it or does not. Where mana comes from is not theirs to know.

## The rules

1. **There is one kind of mana.** A person has one balance (`ManaAccount`)
   and every change to it is one signed row (`ManaEvent`). No pools, no
   colours, no conversion. Sigils and auras regenerate the same mana that
   actions spend.
2. **The doors do not change.** A metered door calls `toto.quota.charge`
   (`price_for`, `check_funds`, `charge`, `refund_for`) as it always did.
   Where Discipline is the host's charger (`settings.QUOTA_CHARGER =
   "discipline"`, or installed without the ledger's rate card) that module
   answers through `discipline.services`. A refusal is a
   `quota.charge.InsufficientFunds` (`NotEnoughMana`, 402) whose text says
   what was needed, what there was, how fast it refills and how else mana is
   come by.
3. **Prices are a file.** `settings.DISCIPLINE_FILE` (YAML; `discipline.yaml`
   here is the default): `cap`, `signup`, `default_regen_per_hour`,
   `prices: {metric code: mana per unit}`, and optionally `auras:` for the
   ingress to seed. Checked at start (`discipline.E001`). A metric that is
   not listed costs nothing.
4. **Regeneration** is hourly (`tasks.regenerate_hour`, on the beat through
   `toto.schedules.beat_schedule(discipline=True)`): a person's rate is the
   **fastest aura** among their communities and the communities above them,
   **plus every active sigil they hold**; it fills to the **aura's cap** (the
   file's `cap` where the aura states none, or there is no aura). Once an
   hour a person (the event's key), and a missed hour is not made up.
5. **An aura** (`Aura`) is an object of its own. A community has one or none
   (`CommunityAura`, one row a community); many communities may have the
   same one, and changing it changes them all.
6. **A sigil** (`Sigil`) is an object of its own: a UUID, a name, an image,
   a rate. An administrator assigns it to one person at a time and may
   revoke it or hand it to another (`sigils.assign`, `sigils.revoke`;
   `SigilEvent` keeps whose hands it passed through). A revoked sigil
   generates nothing from then on; **the mana it generated stays**. Its
   `metadata` is for what a sigil may come to mean beside regeneration.
7. **Grants land whole.** The cap bounds regeneration, not what somebody
   gives or buys: `services.grant` may put a balance above it, where it
   stays until spent.
8. **Sources** (`sources.ManaSource`, a plugin registry) are how mana is come
   by beside regeneration. Here: an administrator's grant, which is a
   superuser's on the Superuser plan, both and never one (`views.may_grant`;
   where no plan is sold, a superuser's). A host with an
   economy registers its own (assets for mana, ending in
   `services.grant(kind="purchase")`); another could stand on an outside
   payment. Nothing else in the platform changes when a source does.
9. **Who administers, and where.** Sigils and auras are made, changed and
   assigned in the **admin panel alone** (`admin.py`), by superusers only:
   a sigil's holder is chosen on its form (cleared, the sigil is revoked),
   an aura is given to a community under "Community auras". The admin
   assigns and revokes through `sigils.assign` and `sigils.revoke`, and
   writes the rest itself with the same audit calls, so the history and the
   audit records are the same. The
   pages Sigils and Auras are read-only lists (the table primitive,
   `oya/partials/table.html`), a superuser's to read. Granting mana is the
   one door on the pages, a superuser's. Everybody signed in reads their own
   mana.
10. **Where mana is seen.** The app bar's chip (a bar: what remains of the
   cap, and the number); the Mana page and its **History** tab (the balance
   over the last day, week or month, one line, drawn with the chart
   primitive from the events, and under it every change, a page of twenty); a person's own profile (`ProfilePlugin`,
   nobody else's); the community directory's Aura column
   (`socialhub.CommunityListPlugin`).

## What a door's charge does

`check_funds` before the work refuses a person who is short. `charge` after
it takes the cost; a balance that fell short in between (two uploads at
once) gives what it has, and the event's `metadata.short` says how much was
missing: work that is done is not failed. `check_and_charge` is strict. A
retried call charges once where the door passes `idempotency_key`.

## Not here

Buying mana (the next stage, in the host with an economy); a price for
encryption (the cipher jobs are not metered); the ledger's three pools
(`toto.mana` in toto-economy), which this does not touch and does not need.
