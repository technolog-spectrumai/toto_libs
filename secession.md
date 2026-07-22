# Secession — which apps leave the shared library, and how

The toto suite carries every app in the shared library, including apps exactly
one host has ever installed. That was the right default while there was one
product; it is the wrong default now that **delta** (e-learning) and
**aurelian** (robot fleet, economy, governance — Neo4j-central) are coming and
will revive parts of `limbo/`.

This document decides, per app, whether it stays shared or secedes to a host
repo; explains the mechanism that makes secession cheap; and records the
migration.

**Status: executed in two waves.**
- **v1.3** — `aster`, `nomad` → faros; `notarius`, `polls`, `travels`, `sketch` → zenobia.
- **v1.4** — `gitvault`, `texlab` → zenobia; `texplay` parked in `limbo/`.

The library is now **38 apps**. Both hosts' clean-env gates pass against `v1.4`
with their own apps loading from their own repos. §6 is the record of how it was
done and the recipe for the next secession.

The analysis was written against v2.0 (post telegraph→forum rework); file:line
references point at that tree, so line numbers for the six moved apps now
resolve in the host repos rather than here.

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

## 2. Census (as analysed at v2.0)

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

### 3.2 Secede to zenobia — `notarius`, `polls`, `travels`, `sketch`

All four are zenobia-only, unreferenced by any planned host, and no library code
imports any of them. Owner today: `toto-base` for all four.

| App | Why it can go |
|---|---|
| `notarius` | Signable/attestable documents. Zero library importers. Aurelian signs contracts through `gervazy` directly (`limbo/contracts/` uses the strongbox), not through notarius. |
| `polls` | Community polls. Zero importers. Aurelian's governance has its own voting stack (`limbo/assembly`). Ships `sql_neo4j_sync/graph/polls.yaml` — see §4.3. |
| `travels` | Human trip planning. Zero importers. Aurelian's fleet uses `locations` Address/Route directly; no limbo app imports travels. |
| `sketch` | Vault SVG drawing. Zero importers. Accepted as a zenobia feature rather than a future delta tool. |
| `texlab` *(v1.4)* | LaTeX compile service. No library importer: `editor/views.py:165-188` reverses `texlab:compile_latex` only behind `is_installed` + `NoReverseMatch`. Live in production (`BUILD_LATEX` is on in both OVH profiles). |
| `gitvault` *(v1.4)* | Git repos over vault dirs. Two library callers remain, both `is_installed`-guarded and function-level: `vault/views.py:104-130` and `editor/views.py:30-42` — see the note below. |

`vod` and `transcription` were candidates here and were **kept in the library** —
see §3.4.

> **Minor hygiene, worth doing anyway.**
> `packages/toto-works/src/toto/manta/views.py:383` does a function-level
> `from toto.transcription.services import transcribe_demo_file` with **no**
> `is_installed` guard. Harmless today — transcription is in `BASE_APPS`, so any
> host following the registry has it — but a host that installs manta without
> transcription would 500 in that demo view. Wrap it in the same
> `apps.is_installed("toto.transcription")` pattern used everywhere else.

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
| `vod`, `transcription` | **delta** | Video lessons with subtitles are a core e-learning need, so both stay shared even though no limbo app imports them today (the one `transcription` reference, `limbo/tariffs/management/commands/ingress_tariffs.py:369`, is `is_installed`-guarded, and `vod` is referenced only by guarded `vault/tests.py`). This is a deliberate judgement about where delta is going, not a technical block — moving them later is easy, moving them back would not be. |
| `kanban` | **aurelian** | `limbo/mission_economy`, `detections`, `mobilization`, `response` all reference Task / Project / Practitioner / Campaign (models, migrations, plugins, ingress commands). |
| `ravioli`, `sql_neo4j_sync`, `bento`, `ingestor`, `neo_editor`, `connectors`, `formica`, `ocr`, `vicuna` | **aurelian** | Aurelian is Neo4j-central: `limbo/robots/graph.py` emits `robot:` / `robot_mission:` graph nodes for the sync, and `limbo/tariffs` + `limbo/metering` reference `ravioli.CypherQuery`. The whole `toto-graph` cluster stays. |
| `sabbia`, `steven` | plausible for both | AI agents over the graph; aurelian's operations layer is the obvious consumer. Keep until a planned host is ruled out. |
| `sso_client` | future hosts | Installed by neither host today because both are SSO *masters*. delta and aurelian federating against zenobia would each need it. Keep. |
| `weather`, `manta`, `antaresia`, `fileservices` | plausible for delta | Notebooks and media pipelines are plausible for a technical e-learning host. Revisit once delta's scope is fixed. |

**`texlab` and `gitvault` were in that row until v1.4, and were moved anyway.**
That is a deliberate override of rule #2, so here is the reasoning rather than a
silently deleted line. Delta does not exist yet and its scope is not fixed; the
limbo delta set (academy, quizzes, library, palimpsest) imports neither app. If
delta later wants LaTeX or git-backed files, the promotion rule in §5.3 applies —
a second host wanting an app is exactly when it returns to the library, and
re-promoting a cohesive app out of a host portion is a copy, not a rewrite.
Against that reversible cost, the certain one is every host carrying both apps
today for a host that may never exist. The same argument would *not* justify
moving `memo`, `vod` or `transcription`, which delta has a concrete claim on.

### 3.5 Orphan — resolved in v1.4

**`texplay`** was installed by no host, absent from `registry.FEATURE_APPS` and
`TASK_MODULES`, mounted by no URLs, and imported by nothing anywhere. Because it
never installed, its `VaultPlayPlugin` never registered — so the LaTeX *Play*
button it provides has not existed in any deployment, while `texlab/README.md`
advertised it. It was **parked in `limbo/texplay/`** (see `limbo/texplay/PARKED.md`)
and the stale doc claims were removed.

A dead-code sweep of `gitvault` and `texlab` at the same time found **nothing** —
every template is included, every URL reversed, every model and field used. They
moved intact.

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
| Celery autodiscovery | `TASK_MODULES` drops `notarius` (transcription stays). | zenobia: `autodiscover_tasks([*TASK_MODULES, "toto.notarius"])` in `celery_app.py`. |
| `registry.BASE_APPS` | Shrinks 19 → 17 (loses notarius, polls). Both hosts hardcode `INSTALLED_APPS`, so nothing consumes it programmatically except zenobia's celery. | Update the constant; it is documentation-of-record. |
| `FEATURE_APPS` | Loses the `travels` and `sketch` keys. | Update; zenobia keeps its own `BUILD_TRAVELS`/`BUILD_SKETCH` blocks. |
| `features.py` flags | Unchanged. `needs_channels` still includes `sketch`, so the host contract is stable even though the app is host-owned. | None. |
| `FAROS_APPS` | No longer meaningful once faros owns aster+nomad. | Retire the constant. |
| `routing.py` | The default list now names only the library's own websocket apps; `toto.texlab.routing` moved out with the app (v1.4). | zenobia's `asgi.py` passes `[*DEFAULT_WEBSOCKET_ROUTING_MODULES, "toto.texlab.routing", "toto.sketch.routing"]`. This also fixed a pre-existing gap: **sketch's websocket had never been collected**, because the library default never listed it and zenobia passed no list. |
| `TASK_MODULES` (v1.4) | Loses `toto.texlab` and `toto.gitvault`. | zenobia: `autodiscover_tasks([*TASK_MODULES, "toto.notarius", "toto.texlab", "toto.gitvault"])`. |
| gitvault's library callers | `vault/views.py:104-130` and `editor/views.py:30-42` keep `is_installed`-guarded, function-level references to a now zenobia-owned app. | Nothing breaks — same shape as `core → nomad`. But note it: this is the first time the shared core reaches into a host-owned app. Four templates that included gitvault's toolbar partial **unguarded** were wrapped in `{% if gitvault_ctx %}` in the same release. |
| `core/views.py:214-236` | The capability map is `is_installed`-based for ~25 apps. | Nothing breaks; seceded apps still report correctly. |

---

## 5. The split (current state at v1.4)

### 5.1 The library — same 7 packages, 38 apps

Nothing is repackaged: the apps that leave keep their package layout behind —
every remaining app keeps its package, its import path and its label. Package names are unchanged, so host pins only
need the version bump.

| Package | Apps after secession | Change |
|---|---|---|
| `toto-base` | api, backup, core, editor, events, gervazy, kanban, locations, memo, people, quota, socialhub, sso_client, sso_core, sso_master, transcription, vault, verbena, vod (19) | −4: notarius, polls, sketch, travels |
| `toto-flow` | mandragora, workflows (2) | — |
| `toto-works` | antaresia, fileservices, manta, weather (4) | −2 to zenobia: gitvault, texlab; −1 to limbo: texplay |
| `toto-chat` | forum (1) | — |
| `toto-ops` | monit (1) | −2: aster, nomad |
| `toto-ai` | sabbia, steven, vicuna (3) | — |
| `toto-graph` | bento, connectors, formica, ingestor, neo_editor, ocr, ravioli, sql_neo4j_sync (8) | — |

`toto-ops` becoming a one-app package is fine — `monit` is installed by both
hosts and by any future one. If it ever feels silly, fold `monit` into
`toto-base`; that is a separate decision with its own MAJOR bump, not part of
this move.

### 5.2 The hosts — their own app code

| Host | Pins (unchanged names, version → `1.4`) | Carried portion |
|---|---|---|
| **zenobia** | all 7 packages `==1.4` | `zenobia/toto/{notarius,polls,sketch,travels,texlab,gitvault}` |
| **faros** | `toto-base`, `toto-flow`, `toto-chat`, `toto-ops` `==1.4` | `faros/toto/{aster,nomad}` |

The portion is plain source, not a distribution — no new package names, no new
pins, nothing for the version gate to check (§4).

### 5.3 The future hosts

Both start the same way: pin the library packages they need, carry their own
apps as a portion, and only promote something into the library when a *second*
host needs it.

| Host | Pins | Carried portion (revived from `limbo/`) |
|---|---|---|
| **delta** (e-learning) | `toto-base` (memo, vod, transcription, verbena, vault, people…), `toto-flow`, likely `toto-works` (texlab/antaresia for technical courses), `toto-chat` | `delta/toto/{academy,quizzes,library,palimpsest}` |
| **aurelian** (fleet + economy) | `toto-base` (kanban, locations, events, quota…), `toto-flow`, `toto-graph`, `toto-ai`, `toto-ops` | `aurelian/toto/{assets,claims,instruments,contracts,tariffs,taxes,invoice,bourse,payroll,loans,insurance,leasing,mission_economy,logistics,assembly,magistrate,tribunal,senate,capitol,treasury,robots,detections,mobilization,response,tactical,inventory}` |

Two limbo apps are needed by **both** future hosts and therefore come back as
**library** apps rather than portions:

- **`competence`** → `toto-base` (academy awards SkillBadges; aurelian's
  `mobilization` matches responder skills against them).
- **`subscriptions`** → `toto-base` (gates academy courses, vod collections, and
  aurelian's recurring revenue alike).

Aurelian's portion is large enough that it may eventually want packaging of its
own — an `aurelian-economy` distribution, host-owned and host-versioned. Note
the naming rule from §4.1: a host-owned distribution must **not** be called
`toto-*`, or `check_runtime_coherence()` will demand it match the suite version.
Keep it as source until a second host wants the economy stack; at that point the
right move is promotion into the library (e.g. a `toto-economy` package), not a
second distribution outside it.

---

## 6. Migration plan

Four waves, each independently verifiable. Waves 1 and 2 are additive — the apps
exist in both places briefly, which is what makes this safe: nothing is deleted
from the library until both hosts are proven green on their own copies.

### Wave 0 — library hygiene ✅

1. Guard the unguarded `manta → transcription` import
   (`toto-works/src/toto/manta/views.py:383`).
2. Fix the accuracy drift in `BUILDING.md:18`, which lists `editor`, `sketch`,
   `travels` and `sso_client` as apps "every host installs unconditionally" —
   the registry is authoritative and says otherwise.

*Gate:* `scripts/clean_env_check.sh` green.

### Wave 1 — faros adopts aster + nomad ✅

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

### Wave 2 — zenobia adopts its four ✅

Same shape for `notarius`, `polls`, `travels`, `sketch`, plus the celery
autodiscovery extension from §4.3. `APPS_TO_SYNC`, `INGRESS_ALLOWED_APPS` and
`DASHBOARD_ITEMS` strings are unchanged.

*Gate:* zenobia `clean_env_test.sh` green across all five profiles.

### Wave 3 — the library sheds them (released as v1.3) ✅

Shipped as **v1.3**. Note the version number does not signal the breakage: a
host that upgrades *without* adopting its portion loses apps, so the upgrade is
only safe when the host adopts its portion in the same step. Both hosts did, in
the same sitting.

1. `git rm` the six app directories.
2. Registry cleanup: `BASE_APPS` −2 (notarius, polls), `FEATURE_APPS` −travels
   −sketch, `TASK_MODULES` −notarius, `FAROS_APPS` retired.
3. `tests/test_packaging.py`: all six movers ship migrations, so the
   migration-app assertion goes 43 → 37 (`tests/test_packaging.py:62`); the
   template floor drops too — recount, don't guess.
4. `scripts/clean_env_check.sh`: refresh the tier matrix (toto-ops becomes
   `{monit}`).
5. `scripts/check_package_graph.py` needs **no** change — membership is derived
   from the filesystem and the pyprojects, so it adapts by itself.
6. `BUILDING.md`: update the package table.
7. `scripts/release.py 1.3`, tag `v1.3`; both hosts bump their pins to `1.3` in
   the same sitting.

*Gate:* library clean-env check, both host gates, both boot smokes.

### What actually happened

All four waves went as written. Final state: the library is **41 apps** across
the same 7 packages at `v1.3`; faros carries `faros/toto/{aster,nomad}` and pins
4 packages; zenobia carries `zenobia/toto/{notarius,polls,travels,sketch}` and
pins 7. Measured, not estimated: migration-apps 43 → 37, templates 245 → 208,
payload 1233 → 1099 entries. Both hosts' gates pass wheel-only — portion loads,
`migrate`, `collectstatic`, boot 302.

Two things worth remembering for the next secession:

- **The placement question answered itself.** The portion goes next to the
  settings package (`<repo>/<project>/toto/`), because that directory is already
  on `sys.path` whenever `DJANGO_SETTINGS_MODULE=<project>.settings` resolves —
  in local `manage.py`, in `deploy.py`, and in the container (`COPY <project>/
  /app/<project>` + `WORKDIR`). **No sys.path, Dockerfile or entrypoint change
  was needed in either host.**
- **setuptools re-shipped deleted apps.** `packages/<pkg>/build/lib/` is reused
  across builds, so the first wheels built after `git rm` still contained all six
  apps — 134 files that no longer existed in `src/`. `build/` is gitignored, so a
  fresh clone or CI would have been fine and the corruption only ever appears on
  a machine that built before the deletion. `scripts/build_wheels.py` now clears
  `build/` per package before building, so this cannot recur. If you ever see a
  wheel containing something you deleted, this is why.

The drill was run too: with `faros/toto/` moved aside, the failure is
`ModuleNotFoundError: No module named 'toto.aster'` — legible, and pointing at
exactly the missing thing.

### Wave 2 (v1.4) — git and tex

`gitvault` and `texlab` → zenobia; `texplay` → `limbo/`. faros was untouched by
the move (it pins no `toto-works`) but still bumped `1.3 → 1.4`: the lockstep
rule means every host tracks every release, whether or not the release affects
its apps. Measured: migration-apps 37 → **34**, templates 208 → **204**, static
6 → **5**, payload 1099 → **1025** entries.

Two things this wave hit that wave 1 did not:

- **A packaging gate that had never moved broke.** `test_packaging.py` asserted
  `len(static) >= 6` and the actual was exactly 6 — `gitvault/static/gitvault/git.js`
  was the sixth file. Refloored to 5. When a wave takes an app that owns a
  *scarce* payload kind, expect the floor asserting it to be exactly at the
  boundary.
- **The first app to leave with library callers still pointing at it** (gitvault;
  see the loose-ends table). Legal, guarded, precedented — but it means
  `toto-base` now has guarded references into *both* host repos.

---

## 7. What this sets up

**delta (e-learning).** The revival set is `academy` (the anchor: Course →
Module → Lesson, each Lesson backed by a `memo.MemoDeck`, exams from
`quizzes.Quiz`, badges from `competence.SkillBadge`), plus `quizzes`, `library`
and `palimpsest` (collaborative multi-author pages). These would form delta's own
portion — or a `toto-learn` package if a second host ever wants them.
`competence` and `subscriptions` return as **shared library** apps: competence is
also used by aurelian's `mobilization`, and subscriptions gates academy courses,
vod collections and aurelian's recurring revenue alike. The current apps delta
pins are **memo** (lesson decks), plus **vod** and **transcription** — video
lessons with subtitles — which is why those three stay in `toto-base`.

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
