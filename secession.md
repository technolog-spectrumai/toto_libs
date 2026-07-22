# Secession — which apps leave the shared library, and how

The toto suite carries every app in the shared library, including apps exactly
one host has ever installed. That was the right default while there was one
product; it is the wrong default now that **delta** (e-learning) and
**aurelian** (robot fleet, economy, governance — Neo4j-central) are coming and
will revive parts of `limbo/`.

This document decides, per app, whether it stays shared or secedes to a host
repo; explains the mechanism that makes secession cheap; and gives the staged
migration plan. It is an analysis and a plan — **no code has moved yet**.

Written against **v2.0** (post telegraph→forum rework). Every claim below was
verified in the tree; file:line references are given so you can re-check them.

---

## 1. The rule

An app may leave the library only when **all three** hold:

1. **Exactly one current host installs it** (zenobia or faros, not both).
2. **No planned host plausibly needs it** — delta and aurelian included. This is
   the expensive test to get wrong: pulling an app back out of a host repo later
   costs more than leaving it shared today.
3. **No library code hard-depends on it.**

"Hard" and "soft" mean what they mean in [BUILDING.md](BUILDING.md) §1:

- **hard** — a module-level import, a `ForeignKey("app.Model")`, a migration
  dependency, or an `AppConfig.ready()` guard. The target must be installed, so
  it must stay in the library (or move together with its dependent).
- **soft** — an import inside a function, under `try/except ImportError`, behind
  `apps.is_installed(...)`, or in an autodiscovered plugin
  (`<app>/plugins/*_plugins.py`, `<app>/predefined_tasks.py`). These are
  optional integrations and **never** block a move.

A useful finding that makes most of this easy: **no ForeignKey, M2M, one-to-one
or migration dependency anywhere in the suite targets any secession candidate.**
The only cross-app model relations in toto point at `vault.VaultFile` and
`people.Person`. Every candidate's coupling is therefore Python-import coupling,
and almost all of it is already guarded.

---

## 2. Census (v2.0)

47 Django apps ship in 7 packages. Counting the `INSTALLED_APPS` region of each
host's settings:

| Set | Count | Apps |
|---|---|---|
| **Both hosts** | 17 | api, backup, core, editor, events, forum, gervazy, locations, mandragora, monit, people, quota, socialhub, sso_core, sso_master, vault, workflows |
| **zenobia only** | 26 | antaresia, bento, connectors, fileservices, formica, gitvault, ingestor, kanban, manta, memo, neo_editor, notarius, ocr, polls, ravioli, sabbia, sketch, sql_neo4j_sync, steven, texlab, transcription, travels, verbena, vicuna, vod, weather |
| **faros only** | 2 | aster, nomad |
| **neither** | 2 | sso_client, texplay |

Two structural notes:

- **faros is a static list.** `faros/faros/faros/settings.py:106-163` hardcodes
  its 19 apps with no `BUILD_*` flags — its app set cannot vary by environment.
  zenobia resolves ~20 flag-gated blocks on top of a 19-app base
  (`zenobia/zenobia/zenobia/settings.py:193-330`).
- **Neither host defines its own Django apps.** There is no `apps.py` anywhere
  in `zenobia/zenobia/` or `faros/faros/`; both hosts are pure composition of
  `toto.*`. Secession creates the first host-owned app code, which is why §4
  spends its time on the mechanism.

---

## 3. Verdicts

### 3.1 Secede to faros — `aster`, `nomad`

Owner today: `toto-ops`. These are Tor infrastructure: `nomad` manages the
app-owned onion identity, `aster` is the signalling directory. The library
already brands them faros-only (`registry.py:65-69`, *"portal must never install
these"*), so shipping them to every host is pure overhead — and neither
e-learning nor a robot fleet implies a Tor host.

Nothing blocks the move. `core` reaches for nomad only through guards:

```python
# packages/toto-base/src/toto/core/views.py:44-51
if apps.is_installed("toto.nomad"):
    try:
        from toto.nomad.service import current_onion
        ...
    except Exception:
        pass
```

with the same shape at :67-77, :87-93, :101-111, and a header comment stating
*"core never hard-depends on nomad"*. `monit`'s Tor and device collectors are
`is_installed`-guarded the same way (`monit/collectors.py:188-192`, `:215-224`).

**After the move `toto-ops` contains only `monit`**, which both hosts install —
so toto-ops becomes a legitimately shared one-app package, and `FAROS_APPS`
retires.

### 3.2 Secede to zenobia — `notarius`, `polls`, `travels`, `sketch`, `vod`, `transcription`

All six are zenobia-only, all six are unreferenced by any planned host, and no
library code imports any of them. Owner today: `toto-base` for all six.

| App | Why it can go |
|---|---|
| `notarius` | Signable/attestable documents. Zero library importers. Aurelian signs contracts through `gervazy` directly (`limbo/contracts/` uses the strongbox), not through notarius. |
| `polls` | Community polls. Zero importers. Aurelian's governance has its own voting stack (`limbo/assembly`). Ships `sql_neo4j_sync/graph/polls.yaml` — see §4.3. |
| `travels` | Human trip planning. Zero importers. Aurelian's fleet uses `locations` Address/Route directly; no limbo app imports travels. |
| `sketch` | Vault SVG drawing. Zero importers. Accepted as a zenobia feature rather than a future delta tool. |
| `vod` | Video on demand. Only `vault/tests.py` references it, `is_installed`-guarded. |
| `transcription` | Audio→text. One lazy importer, see the hygiene item below. |

No limbo app imports `vod`; the single limbo reference to `transcription`
(`limbo/tariffs/management/commands/ingress_tariffs.py:369`) is itself
`is_installed`-guarded. Neither is pinned by a future host.

> **Hygiene item, do this before the move.**
> `packages/toto-works/src/toto/manta/views.py:383` does a function-level
> `from toto.transcription.services import transcribe_demo_file` with **no**
> `is_installed` guard. It cannot break startup, but on a host with manta and
> without transcription that demo view 500s at request time. Wrap it in the same
> `apps.is_installed("toto.transcription")` pattern used everywhere else. This
> matters as soon as transcription is zenobia-owned and manta stays in the
> library.

### 3.3 Stay — hard-pinned by library code

**`verbena`** is the one candidate that cannot leave. `socialhub` — installed by
every host — imports it at module level:

```
packages/toto-base/src/toto/socialhub/models.py:17   AbstractSection, AbstractTag
packages/toto-base/src/toto/socialhub/models.py:18   unique_slug
packages/toto-base/src/toto/socialhub/forms.py:15    apply_oya_field_styles
packages/toto-base/src/toto/socialhub/admin.py:4     make_section_form
```

plus `kanban` (models/views/admin) and `mandragora/forms.py:4`. verbena is
infrastructure for the platform's page/section/tag model, not a feature.

### 3.4 Stay — pinned by a planned host

These are zenobia-only *today* and would otherwise look movable. They are not.

| App | Pinned by | Evidence |
|---|---|---|
| `memo` | **delta** | `limbo/academy/models.py:9` — `from toto.memo.models import MemoDeck`; every academy Lesson is backed by a MemoDeck, and `limbo/library` tags with `memo.Tag`. |
| `kanban` | **aurelian** | `limbo/mission_economy`, `detections`, `mobilization`, `response` all reference Task / Project / Practitioner / Campaign (models, migrations, plugins, ingress commands). |
| `ravioli`, `sql_neo4j_sync`, `bento`, `ingestor`, `neo_editor`, `connectors`, `formica`, `ocr`, `vicuna` | **aurelian** | Aurelian is Neo4j-central: `limbo/robots/graph.py` emits `robot:` / `robot_mission:` graph nodes for the sync, and `limbo/tariffs` + `limbo/metering` reference `ravioli.CypherQuery`. The whole `toto-graph` cluster stays. |
| `sabbia`, `steven` | plausible for both | AI agents over the graph; aurelian's operations layer is the obvious consumer. Keep until a planned host is ruled out. |
| `sso_client` | future hosts | Installed by neither host today because both are SSO *masters*. delta and aurelian federating against zenobia would each need it. Keep. |
| `weather`, `manta`, `texlab`, `antaresia`, `gitvault`, `fileservices` | plausible for delta | LaTeX, notebooks, git-backed files and media pipelines are exactly what a technical e-learning host wants. Revisit once delta's scope is fixed. |

### 3.5 Orphan, left as-is

**`texplay`** (`toto-works`) is installed by neither host, absent from
`registry.FEATURE_APPS` and `TASK_MODULES`, and only survives in stale docs that
claim it ships with `BUILD_LATEX` (`zenobia/README.md:69`, `BUILDING.md:20`,
`README.md:115`). It is dead weight today. Left in place by decision; when you
touch it next, either park it in `limbo/` or wire it back under `BUILD_LATEX` —
and fix those three doc lines either way.

---

## 4. The mechanism: host-carried namespace portions

A seceded app does **not** become a new pip package and does **not** change its
import path. The host repo simply carries `toto/<app>/` as plain source on
`sys.path`, and PEP 420 merges it with the installed wheels:

```
faros/
  toto/                 <- host-carried portion, NO __init__.py
    aster/
    nomad/
  faros/                <- the Django project
```

### 4.1 Why this works

- **The namespace is clean.** No `toto/__init__.py` exists in any package
  source, any build tree, or the installed venv — verified. The installed
  `site-packages/toto/` already merges portions from four separate wheels into
  one directory.
- **Source and wheels merge.** Verified experimentally: with `toto-base`
  installed and a source dir on `PYTHONPATH`, `import toto.registry` and
  `import toto.<portion-app>` both resolve and `toto.__path__` carries two
  entries.
- **Nothing else changes.** App labels, migration history, `INSTALLED_APPS`
  strings, `apps.is_installed("toto.nomad")`, `reverse("nomad:connect_qr")`,
  templates and static all behave identically — they never knew which
  distribution provided the module.
- **The version gate ignores it.** `packages/toto-base/src/toto/versioning.py`
  `check_runtime_coherence()` scans installed distributions named `toto-*`. A
  source portion has no distribution metadata, so it is invisible and can never
  trip the lockstep check. (A host-owned *dist* would only be safe if it is not
  named `toto-*`, or is kept at the suite version — another reason to prefer
  plain source.)
- **The build system is untouched.** Host trees are already COPY'd into the
  image, rsynced on push and bind-mounted locally. No wheel staging, no
  `requirements.toto.txt` change, no `--smart` fingerprint change.

### 4.2 The one thing to get right

The portion directory must be on `sys.path` in **all four** execution modes:
local `manage.py`, `deploy.py`, the container, and the clean-env gate. Today the
host repo root is already on the path in each of those
(`faros/faros/manage.py:24-31`; `Dockerfile` `COPY faros/ /app/faros` with
`WORKDIR /app/faros`), so placing `toto/` at the repo root is the low-friction
choice — but it must be verified in the image, not assumed.

If it is ever missing, Django fails with a clear
`No installed app with label 'aster'` rather than something cryptic. The
migration plan drills this deliberately.

### 4.3 Loose ends the plan must handle

| Item | What happens | Action |
|---|---|---|
| `graph/polls.yaml` | Stays in `toto-graph`. `sql_neo4j_sync/loader.py:22-36` calls `get_app_config(app_label)` and `continue`s on `LookupError`, so a config for an uninstalled app is skipped silently. | Nothing breaks. Optionally move the yaml into zenobia's portion later. |
| Celery autodiscovery | `TASK_MODULES` drops `transcription` and `notarius`, but zenobia still runs their tasks. | zenobia: `autodiscover_tasks([*TASK_MODULES, "toto.transcription", "toto.notarius"])` in `celery_app.py`. |
| `registry.BASE_APPS` | Shrinks 19 → 15 (loses notarius, polls, vod, transcription). Both hosts hardcode `INSTALLED_APPS`, so nothing consumes it programmatically except zenobia's celery. | Update the constant; it is documentation-of-record. |
| `FEATURE_APPS` | Loses the `travels` and `sketch` keys. | Update; zenobia keeps its own `BUILD_TRAVELS`/`BUILD_SKETCH` blocks. |
| `features.py` flags | Unchanged. `needs_channels` still includes `sketch`, so the host contract is stable even though the app is host-owned. | None. |
| `FAROS_APPS` | No longer meaningful once faros owns aster+nomad. | Retire the constant. |
| `routing.py` | Imports routing modules inside `try/except (ImportError, AttributeError)` (`:27-31`). | Nothing breaks. |
| `core/views.py:214-236` | The capability map is `is_installed`-based for ~25 apps. | Nothing breaks; seceded apps still report correctly. |

---

## 5. Migration plan

Four waves, each independently verifiable. Waves 1 and 2 are additive — the apps
exist in both places briefly, which is what makes this safe: nothing is deleted
from the library until both hosts are proven green on their own copies.

### Wave 0 — library hygiene (release as v2.x)

1. Guard the unguarded `manta → transcription` import
   (`toto-works/src/toto/manta/views.py:383`).
2. Fix the accuracy drift in `BUILDING.md:18`, which lists `editor`, `sketch`,
   `travels` and `sso_client` as apps "every host installs unconditionally" —
   the registry is authoritative and says otherwise.

*Gate:* `scripts/clean_env_check.sh` green.

### Wave 1 — faros adopts aster + nomad

1. Copy `packages/toto-ops/src/toto/{aster,nomad}` to `faros/toto/` (plain copy;
   the history stays in toto_libs, which remains the origin of record).
2. Confirm `faros/toto` resolves in all four modes (§4.2) — especially inside
   the built image.
3. `INSTALLED_APPS`, urls, middleware and settings blocks stay **byte-identical**.
4. Extend `faros/scripts/clean_env_test.sh` so the wheel-only run proves the
   portion apps migrate and boot.

*Gate:* faros `clean_env_test.sh` green, boot smoke 302, onion identity works.
*Drill:* temporarily remove the portion from `sys.path`; confirm the failure is
`No installed app with label 'nomad'`.

### Wave 2 — zenobia adopts its six

Same shape for `notarius`, `polls`, `travels`, `sketch`, `vod`, `transcription`,
plus the celery autodiscovery extension from §4.3. `APPS_TO_SYNC`,
`INGRESS_ALLOWED_APPS` and `DASHBOARD_ITEMS` strings are unchanged.

*Gate:* zenobia `clean_env_test.sh` green across all five profiles.

### Wave 3 — the library sheds them (release v3.0)

**MAJOR**, because `BASE_APPS` shrinks — a host that upgrades without adopting
its portion loses apps, which is exactly what a major bump is for.

1. `git rm` the eight app directories.
2. Registry cleanup: `BASE_APPS` −4, `FEATURE_APPS` −travels −sketch,
   `TASK_MODULES` −transcription −notarius, `FAROS_APPS` retired.
3. `tests/test_packaging.py`: the migration-app count (currently asserted at 43)
   and the template floor both drop — recount, don't guess.
4. `scripts/clean_env_check.sh`: refresh the tier matrix (toto-ops becomes
   `{monit}`).
5. `scripts/check_package_graph.py` needs **no** change — membership is derived
   from the filesystem and the pyprojects, so it adapts by itself.
6. `BUILDING.md`: update the package table.
7. `scripts/release.py 3.0`, tag `v3.0`; both hosts bump their pins to `3.0` in
   the same sitting.

*Gate:* library clean-env check, both host gates, both boot smokes.

---

## 6. What this sets up

**delta (e-learning).** The revival set is `academy` (the anchor: Course →
Module → Lesson, each Lesson backed by a `memo.MemoDeck`, exams from
`quizzes.Quiz`, badges from `competence.SkillBadge`), plus `quizzes`, `library`
and `palimpsest` (collaborative multi-author pages). These would form delta's own
portion — or a `toto-learn` package if a second host ever wants them.
`competence` and `subscriptions` return as **shared library** apps: competence is
also used by aurelian's `mobilization`, and subscriptions gates academy courses,
vod collections and aurelian's recurring revenue alike. The only current app
delta pins is **memo**.

**aurelian (robot fleet, economy, governance).** `limbo/economy.md` already
documents the seven-layer revival order — ledger (`assets`) → instruments
(`claims`, `instruments`, `contracts`) → billing (`tariffs`, `taxes`, `invoice`)
→ peer economy (`bourse`, `payroll`, `loans`, `insurance`, `leasing`,
`mission_economy`) → commerce (`logistics`) → governance (`assembly`,
`magistrate`, `tribunal`, `senate`, `capitol`, `treasury`) → operations
(`robots`, `detections`, `mobilization`, `response`, `tactical`, `inventory`).
Follow that order; the layers are tightly self-referential and resist partial
revival. Aurelian pins `kanban`, `locations`, `events`, `quota`, `vault`,
`workflows` and the entire graph cluster — which is the substantive reason
`toto-graph` must stay in the library rather than following zenobia.

**Dead weight in limbo.** `bazaar` (community retail — no planned host wants it),
`old_steven` (superseded by the current `toto-ai/steven`) and `metering`
(a thin facade over `tariffs`; the active `quota` replaced it) can be deleted
outright — git keeps the history. `incidents` is thin, has no README and overlaps
`detections`; merge rather than revive.

**The shape this converges on.** The library keeps what more than one host needs
or will need: the platform core, the graph cluster, and the cross-cutting
infrastructure. Each host owns its own idiosyncrasies as source portions. New
hosts start by pinning `toto-base` and adding a portion, not by negotiating a
place for their apps in someone else's release.
