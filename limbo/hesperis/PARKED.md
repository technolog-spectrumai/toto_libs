# toto.hesperis — parked 2026-08-29

The Bounty Board: crowdsourced collection over the kanban work engine. Six
models — `HesperisCampaign` and `HesperisBounty` extending `kanban.Campaign`
and `kanban.Mission`, `AcceptedObservation` as the durable fact a submission
became once review accepted it, and `Dataset` / `DatasetVersion` /
`DatasetVersionMember` for publishing that work as numbered, hash-stamped
releases. Around them a real management surface (create a campaign, create and
enable bounties, work a review queue, distribute rewards, freeze a version),
`integration/ledger.py`'s per-dataset tamper-evident chain, an ingress seeder,
and 1,303 lines of tests across six modules with their own
`testing/settings.py` harness.

It was well built. `zenobia/features.md` called it *"the most carefully built
thing in the teamwork area"* — and, in the same sentence, *"off in six of
seven"* configurations.

## Why it was parked

Succeeded by **`toto.lacedo`** — Bounties — a host portion at
`zenobia/zenobia/toto/lacedo/`. Not a rewrite for its own sake: the product
question changed, and hesperis answered the old one correctly in four places
where the new answer is the opposite.

* **Datasets.** Half of hesperis' tables are dataset machinery, and the
  platform's dataset story moved out of the application entirely — to lakeFS,
  or to plain vault buckets. Lacedo has no dataset model, no dataset foreign
  key and no code that interprets what a link points at. Note the consequence:
  `Dataset` and `DatasetVersion` were **the only dataset models anywhere on
  this platform**, so parking this app removes dataset semantics from the
  product until they arrive separately.
* **Claiming.** Contributing here minted a `kanban.Assignment` — a live,
  exclusive claim on a slot. Lacedo is deliberately non-exclusive: its
  `WorkingNotice` is a courtesy signal that grants its author nothing.
* **Who decides.** hesperis decided by counting reviews against an editable
  `ConsensusPolicy`. In Lacedo a Lodge decides because it is the Lodge, and
  publishes a `DecisionNote` saying what it decided and why.
* **What a reward is.** `kanban.RewardGrant` is one submission × one person,
  fired by a trigger. A Lacedo grant is discretionary, issued after the fact,
  and may recognise several contributions and several people at once.

One more difference is worth recording because it is a design lesson rather
than a preference. **hesperis cannot boot without `toto.assets`** — its
`apps.py` raises `ImproperlyConfigured` on purpose, because
`HesperisCampaign.gem_asset` and `.treasury_account` are real foreign keys into
the ledger and the alternative is a `fields.E300` storm on every command. Those
two columns were barely used: `gem_asset` is never read at all, and
`treasury_account` only for its `.code`. Lacedo stores the asset as a **symbol
string**, exactly as `kanban.RewardPolicy` does, and therefore installs and runs
on a host with no economy at all.

## Its tables are deliberately left in place

`hesperis_hesperiscampaign`, `hesperis_hesperisbounty`,
`hesperis_acceptedobservation`, `hesperis_dataset`, `hesperis_datasetversion`,
`hesperis_datasetversionmember`, and their `django_migrations` rows.

They hold real contributions, real review outcomes and real published releases,
and unlike a rename or a split there is nothing here that needs the schema to
change. **It is not in `zenobia/scripts/drop_departed_tables.py`, and it must
not be added** — the same call `limbo/polls/PARKED.md` and
`zenobia/limbo/quizzes/PARKED.md` make, for the same reason. Nothing recreates
these tables, and nothing else references them.

## What breaks while it is parked

Nothing on the platform depends on it — no app holds a foreign key into
hesperis, and nothing imports it.

What goes is `/hesperis/` and its fifteen route names, the review queue, the
dataset pages and their chain verification. `KANBAN_REWARD_BACKEND` in
`zenobia/settings.py` is **unaffected**: it is kanban's, and kanban still has
rewards of its own.

`toto-works` also dropped its `toto-economy` dependency with this move — that
edge existed solely for hesperis' foreign keys into `assets`.

## Reviving it

The reverse of the commit that parked it:

1. `git mv vendor/toto_libs/limbo/hesperis vendor/toto_libs/packages/toto-works/src/toto/hesperis`
2. Restore `toto-economy` to `toto-works/pyproject.toml` — the FKs need it, and
   `check_package_graph.py` will fail without it.
3. `toto/features.py`: the `hesperis: bool` dataclass field, the resolution
   (including the `if hesperis and not kanban: raise FeatureConfigError` guard),
   and the constructor argument. `toto/registry.py`: the `FEATURE_APPS` entry.
4. `zenobia/settings.py`: `BUILD_HESPERIS`, the `INSTALLED_APPS` block, the
   `INGRESS_ALLOWED_APPS` entry, and — if it is to have a door — a
   `DASHBOARD_ITEMS` tile **with a title that is not "Bounties"**, since
   `toto.lacedo` holds that name now and `core.dashboard_view` keys tiles by
   their untranslated English title.
5. `zenobia/urls.py`: the `_advanced` row.
6. The builder: a `Capability` in `zenobia/tools/zenobia_builder/spec.py`
   requiring the Work axis, plus its `extra_checks` orphan rule.
7. The gate: `BUILD_HESPERIS=1` in the host-owned block, the two host test
   modules, `/hesperis/` in `SMOKE_PATHS_EXTRA`, and the `==> hesperis suite`
   stanza that runs its own harness.
8. `vendor/toto_libs/tests/test_packaging.py`: bump the apps-with-migrations
   count back up by one.
