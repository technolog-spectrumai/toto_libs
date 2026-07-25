# toto

**toto** is a modular Django *app library* — a "community operating system" —
shipped as **ten lockstep-versioned pip packages that share the single
`toto.*` import namespace**, released together from this one repository. It
bundles ~38 apps spanning identity and single sign-on, encrypted storage, a
Neo4j knowledge graph, real-time collaboration, media/transcription pipelines,
workflow automation and pluggable AI. toto is never run on its own: a **host
project** supplies the settings, URLs and server entrypoint and mounts exactly
the subset of apps it needs through per-feature build flags. The two current
hosts are **zenobia** (the full management portal — the reference deployment)
and **faros** (a minimal Tor-only profile); future hosts **delta**
(e-learning) and **aurelian** (fleet + economy + governance) are on the way.

This document is the suite-level manual. Each package carries its own README
with app-by-app detail — this file does not repeat that; it links to them.

---

## What it does (functional)

toto is a construction kit for community platforms. A host assembles a running
site out of toto's building blocks; an operator then gets, depending on which
blocks are enabled:

- **One identity for everything.** Every person is a single account that every
  feature recognises. The platform is itself a full **OpenID Connect provider**,
  so the same login also signs people in to other toto services and to external
  tools (e.g. Grafana), and other deployments can federate against it. Hosts
  can also offer "Continue with Google/Facebook" via `toto.social_login`.
- **Communities and membership.** Referral-gated communities with hierarchy,
  news and administration, plus scheduled events and a personal availability
  calendar.
- **Encrypted storage by default.** Per-user file storage (buckets, folders,
  sharing links, soft delete) sits on top of a centralized encryption-at-rest
  vault: every secret, key and encrypted file the platform holds is protected by
  a password-derived key hierarchy, never stored in plaintext or a config file.
- **Real-time collaboration.** Persistent, searchable chat channels; shared
  collaborative text/JSON/LaTeX editing of stored files; sandboxed in-browser
  Python execution; and Jupyter-style compute notebooks — all over WebSockets.
- **A knowledge graph.** An opt-in Neo4j layer where text, uploaded documents,
  screenshots (OCR) and external APIs are turned into reviewable, validated
  graph patches; saved Cypher queries, network analysis and import/export; and an
  autonomous "colony" agent that curates the graph over time.
- **Media and documents.** Audio/video transcription (local Whisper) with
  subtitle export, video-on-demand playback, and ffmpeg-based file processing
  pipelines.
- **Automation.** A DAG workflow engine that runs scheduled jobs — weather
  ingestion, report generation, media processing — on background workers.
- **Pluggable AI.** Chat agents and a site-wide "Ask AI" widget backed by either
  hosted models or a local Ollama deployment, with graph-aware (GraphRAG)
  retrieval over the knowledge graph.
- **Project management, quotas, backups, monitoring.** Projects → tasks with
  scheduled allowances, usage-quota policies, signed backup archives, and a
  read-only operations dashboard.

The defining trait is **composability**: a deployment is a choice of features,
not a code branch. The same source tree ships as a lean WSGI + Postgres site or
as a full ASGI stack with Neo4j, background workers, a compute-kernel server and
AI — decided entirely by the host's build flags.

---

## How it works (technical)

### One namespace, many distributions (PEP 420)

toto ships as **ten pip distributions that all fill the same `toto.*` import
namespace**. This is a deliberate PEP 420 namespace package: there is **no
`toto/__init__.py`** anywhere — not in any package source, build tree, or the
installed `site-packages/toto/`. `import toto.vault` resolves the same whether
one wheel or four provide portions of the namespace, and app labels, migration
history and `INSTALLED_APPS` strings are identical regardless of which
distribution ships a given app. Splitting the repo into packages changed no
import path.

The package boundaries and what each holds:

| Package | Depends on | Holds (see the package README for app detail) |
|---|---|---|
| [`toto-base`](packages/toto-base/README.md) | — | The shared host API (`features`, `registry`, `routing`, `schedules`, `conf`, `celery_utils`, `versioning`), the `ui` and `ingress` infrastructure, and the app cluster every host installs: `core`, `api`, `backup`, `gervazy`, `vault`, `people`, `socialhub`, `events`, `locations`, `verbena`, `quota` — plus `editor`, which ships here but is host-selected rather than unconditional. |
| [`toto-auth`](packages/toto-auth/README.md) | base | `sso_core`, `sso_master` (OIDC provider), `sso_client` (OIDC consumer), `social_login` (Google/Facebook sign-in) and the `toto.auth_config` login-strategy resolver: each host picks provider/consumer/local via settings. |
| [`toto-flow`](packages/toto-flow/README.md) | base | `workflows` (DAG engine) and `mandragora` (WebSocket compute kernels) — one unit; `workflows.LambdaFunction` has a one-to-one key into `mandragora.ComputeKernel`. |
| [`toto-works`](packages/toto-works/README.md) | base, flow | `antaresia` (sandboxed vault Python), `kanban` (projects → tasks with scheduled allowances), `memo` (`.pml` presentations). |
| [`toto-geo`](packages/toto-geo/README.md) | base, flow | `weather` (observations/forecasts keyed off `locations.Address`, populated by workflow nodes). |
| [`toto-media`](packages/toto-media/README.md) | base, flow | `manta`, `transcription`, `vod`, `fileservices` — the ffmpeg/Whisper video-media stack (`BUILD_MEDIA`). |
| [`toto-chat`](packages/toto-chat/README.md) | base | `forum` (persistent, full-text-searchable Channels chat). |
| [`toto-ops`](packages/toto-ops/README.md) | base | `monit` (read-only operations/monitoring dashboard and collectors). |
| [`toto-ai`](packages/toto-ai/README.md) | base | `sabbia` (headless agent backend, GraphRAG), `steven` (site-wide Ask-AI widget), `vicuna` (Ollama deployment registry). |
| [`toto-graph`](packages/toto-graph/README.md) | base, flow, ai | `ravioli` (the sole Neo4j boundary), `sql_neo4j_sync`, `bento`, `ingestor`, `neo_editor`, `ocr`, `connectors`, `formica`. |

### The hard-vs-soft partition rule

Package membership is not a taxonomy — it is derived from the dependency graph
by one rule with two cases:

- A **hard** edge — a module-level import, a `ForeignKey("app.Model")`, a
  migration dependency, or an `AppConfig.ready()` guard — means the target
  *must* be installed, so it must live in the same package or one the dependant
  already requires.
- A **soft** edge — an import inside a function, under `try/except ImportError`,
  behind `apps.is_installed(...)`, or in an autodiscovered plugin
  (`<app>/plugins/*_plugins.py`, `<app>/predefined_tasks.py`) — is an optional
  integration and must **never** become an install dependency.

This is why `toto-base` is large: `gervazy` foreign-keys `people.Person`,
`people` imports `toto.core`, and `locations`/`events` import each other — one
irreducible cycle that every host installs anyway, so it cannot be layered
apart. `locations` in particular is fused into base and cannot be extracted.
Conversely, `toto-graph` depends on `toto-ai` (not the reverse): the graph's AI
integrations are hard, but AI's graph integrations are lazy on purpose.
`scripts/check_package_graph.py` re-derives membership from the filesystem and
the dependency DAG from the pyprojects on every run and fails the build if a
hard edge crosses an undeclared boundary (see **Build & packaging**).

### Key design decisions and couplings

- **`Person` is the identity anchor.** Nearly every app links to
  `people.Person` (one-to-one with `auth.User`, nullable) rather than to `User`
  directly. The only cross-app model relations in the entire suite point at
  `people.Person` and `vault.VaultFile` — everything else is (guarded) Python
  import coupling. This is what makes apps cheap to move between packages or out
  to a host.
- **Encryption at rest is centralized in `gervazy`.** A three-tier AES-256-GCM
  key hierarchy (password → Argon2id → user KEK → vault master key → data
  encryption keys → objects), Ed25519 signing and an append-only crypto audit
  log. Any app needing a secret, key or encrypted file stores it in gervazy.
- **`ravioli` is the sole Neo4j boundary.** No other app touches the Bolt driver
  or neomodel. The graph *shape* is declared as YAML in `sql_neo4j_sync`; writes
  go through ravioli's per-object "Export to graph" (checksum-diffed,
  history-snapshotted) or the opt-in bulk projection. `bento`, `ingestor`,
  `connectors` and `formica` all reach Neo4j through it.
- **`sso_master` is a full OIDC 1.0 provider** (authorize/token/userinfo/JWKS,
  PKCE, relying-party registration); its RSA signing key is stored encrypted in
  gervazy. `sso_client` is the consumer side, for hosts that federate rather than
  provide. Both ship in `toto-auth`, and `toto.auth_config` picks the mode per
  host (provider/consumer/local) from settings.
- **Real-time is Django Channels.** Chat, collaborative editors, sandboxed
  Python, compute kernels and the AI widgets are WebSocket consumers; enabling
  any of them flips the host to ASGI.
- **Per-feature composition.** A deployment is defined by `BUILD_*`/`INSTALL_*`
  flags resolved in host settings, not by branches; the same tree can ship
  WSGI + Postgres or ASGI + Neo4j + Celery + kernel server + AI.

### Making GIS optional (BUILD_GEO)

`toto.locations` (in `toto-base`) is a GeoDjango app, so by default every host
must ship the whole GDAL / GEOS / PROJ / spatialite–or–PostGIS stack. `BUILD_GEO`
(default **on**) makes that optional: a host built with `BUILD_GEO=0` keeps
`locations` installed but **geometry-less**, runs on a plain sqlite/postgres
backend, and drops the native GIS toolchain from its image — a much lighter
host (this is what `faros` uses).

What makes it tractable: the suite has **zero spatial SQL** — no
distance/within/contains/intersects lookups anywhere, only `isnull` filters and
raw `.x/.y/.geojson` access — so a non-spatial backend is viable. The switch is
`features.geo`, surfaced to Django as `settings.HAS_GIS`, and it drives:

- **`locations/models.py`** — imports the plain ORM instead of `contrib.gis.db`
  and omits every geometry field (`Address.geometry`, `Territory/Zone/Route`,
  `MapLayerPolygon.geometry`/`center`). `Address` always carries plain
  `latitude`/`longitude` floats as the canonical coordinate store; on a GIS build
  `Address.save()` keeps them and `geometry` in sync. The geometry-bearing models
  survive as **geometry-less stub tables**, so cross-app FKs into them
  (`socialhub.Community.territory`, `kanban` → `Zone`/`Route`) still resolve.
- **`core/base_admin.py`** — `TotoGeoAdmin` falls back from `OSMGeoAdmin` to a
  plain `ModelAdmin`, removing the one GIS import that admin autodiscovery drags
  in at startup on every host.
- **Migrations** — a second, hand-maintained graph
  `locations/migrations_nogis/` (selected via `MIGRATION_MODULES`) creates the
  same seven tables minus geometry, reusing node names so every cross-app
  migration dependency (`people`, `events`, `socialhub`, `kanban`, `weather` →
  `('locations','0001_initial')`) resolves unchanged. The two graphs are kept in
  lockstep; `tests/test_django_check.py` runs `makemigrations --check` in both
  modes to catch drift, and proves the GIS-off path boots and migrates with the
  `contrib.gis` import blocked outright.
- **Map-dependent apps require GIS.** `resolve_features` raises if
  `BUILD_WEATHER`/`BUILD_TRAVELS` is set with `BUILD_GEO=0` (they read geometry
  and render map overlays) — an explicit build-time error rather than a silent
  re-enable.

GIS-off is **greenfield only**: it targets fresh databases; there is no in-place
conversion of an existing PostGIS database.

### The host integration API (`toto.*`, in `toto-base`)

Hosts consume small stable modules instead of hardcoding internals:

- **`toto.features`** — `resolve_features(get)` turns `BUILD_*`/`INSTALL_*` flags
  (from `os.environ.get` or a deploy-config dict) into effective feature
  booleans, tiers and native-binary needs; the single source for the dependency
  closure.
- **`toto.registry`** — `CORE_APPS`/`AUTH_APPS`/`BASE_APPS`, `FEATURE_APPS`,
  `TASK_MODULES` (Celery autodiscovery) and `has_app()`.
- **`toto.auth_config`** (ships in `toto-auth`) — `resolve_auth(get)` turns
  `TOTO_AUTH_MODE` (`local`/`provider`/`consumer`) and the auth knobs
  (`SSO_OPEN_REGISTRATION`, cooldowns, `TOTO_LOGIN_REDIRECT`) into a frozen
  `AuthConfig`; `auth_apps(cfg)`, `auth_urlpatterns(cfg)`, `login_url(cfg)` and
  `authentication_backends(cfg)` feed it into `INSTALLED_APPS`, the url tree,
  `LOGIN_URL` and `AUTHENTICATION_BACKENDS`. Every mode keeps the `sso` url
  namespace alive, so `LOGIN_URL = "sso:login"` holds everywhere.
- **`toto.routing`** — `collect_websocket_urlpatterns()` gathers Channels routes
  from installed toto apps for the host ASGI router.
- **`toto.schedules`** — `beat_schedule(...)` builds Celery-beat entries for the
  enabled features.
- **`toto.conf`** — host-configurable filesystem locations; hosts set
  `TOTO_DATA_DIR` (seed/branding data) and `TOTO_RUN_DIR` (vault-password
  bundles).
- **`toto.versioning`** — the version contract (`read_manifest`,
  `verify_checkout`, `verify_wheels`, `check_runtime_coherence`). The suite
  version comes from distribution metadata
  (`importlib.metadata.version("toto-base")`); there is deliberately **no
  `toto.__version__`**, because `toto` is a namespace shared by several
  distributions.

### The runtime version-coherence guard

Because the ten distributions release lockstep, mixing versions is a bug.
`toto.core`'s AppConfig calls `check_runtime_coherence()` at boot: it scans the
installed `toto-*` distributions and raises `ImproperlyConfigured` if their
versions differ, or if a **pre-split single `toto` distribution** is still
installed and shadowing the namespace. Host-carried source portions (see
**Architecture history**) have no distribution metadata, so they are invisible
to this check by design. Set `TOTO_SKIP_VERSION_CHECK=1` to bypass it for local
experiments.

---

## Usage

### Install for development (editable)

Install all ten packages editable, in **one** pip invocation — they pin each
other exactly, so pip can only satisfy the sibling pins when it sees every
package at once:

```bash
scripts/install_toto.sh
```

If you are coming from the old pre-split single distribution, remove it first —
it shadows the namespace packages:

```bash
pip uninstall -y toto
scripts/install_toto.sh
```

### Build wheels and install offline (no package index)

```bash
git clone <any-host>/toto_libs.git && cd toto_libs
git checkout v1.6
python scripts/build_wheels.py                       # all packages -> dist/
python scripts/build_wheels.py --only toto-base,toto-flow
python scripts/build_wheels.py --sdist               # sdist first, then wheel FROM it
```

`build_wheels.py` clears each package's stale `build/` tree before building (so
a file deleted from `src/` can never keep shipping) and prints the exact install
line for the target machine:

```bash
pip install --no-index --find-links dist toto-base==1.6 toto-flow==1.6
```

`--no-index` is how hosts install: pip resolves the exact sibling pins against
the staged wheels alone and never touches the network.

### Develop against a host

- **Day to day:** hack here with everything installed editable; hosts keep
  pointing at their pinned tag, so your work in progress cannot reach a
  deployment by accident.
- **Test unreleased library code in a host's Docker stack:** use the dev escape
  hatch (`deploy.py <config> up --dev` / `./deploy_local.sh --dev`) — allowed for
  local bring-up, refused for anything that leaves the machine.
- **Deploy while mid-feature:** keep the pinned tag in a second working tree so
  you never have to stash, and point the host at it —
  `git worktree add ../toto_libs_stable v1.6`, then `TOTO_SRC=../toto_libs_stable`
  (or `toto_src:` in the deploy config).

### Gates you run locally

- `scripts/check_package_graph.py` — the package partition (membership, acyclic
  DAG, no undeclared hard edge, versions coherent).
- `scripts/release.py --check` — VERSION equals every package version and every
  sibling pin.
- `scripts/clean_env_check.sh` — the full clean-environment proof (see below).
  Run it with the system interpreter — `toto.locations` needs the system GDAL,
  which a conda python usually cannot load:
  `PYTHON=/usr/bin/python3 scripts/clean_env_check.sh`.

Build and deployment themselves are owned by the host project: each host stages
the wheels it pins through its own `deploy.py`, which rejects the build if the
checkout does not match those pins.

---

## Build & packaging

The build system exists to make one thing true: **you can develop toto freely
and deploy a host without fear**, because a host only ever builds against the
exact library version it declares, and every layer of the toolchain refuses
anything else.

### Lockstep MAJOR.RELEASE versioning

Versions are `MAJOR.RELEASE` — two integers, e.g. `1.6` (the current suite
version lives in the `VERSION` file, the single source of truth). One repository
tag `vX.Y` releases the whole suite: every package is built at exactly `X.Y` and
pins its siblings to exactly `X.Y`. "Check out `v1.6`" is therefore a complete,
unambiguous instruction — there is no per-package version matrix.

- Bump **MAJOR** when hosts must change something: a settings contract, a host
  API signature, an app label, or a non-backwards-compatible migration.
- Bump **RELEASE** for everything else, including fixes.

Because the rule is lockstep, **every host tracks every release** whether or not
it touches that host's apps — but hosts pick a release up only when *they*
choose, by editing their manifest. Nothing upgrades on its own.

### Cutting a release

```bash
git status --porcelain                     # must be empty
PYTHON=/usr/bin/python3 scripts/clean_env_check.sh
python scripts/release.py 1.6              # rewrites VERSION + every package version + every sibling pin
git commit -am "release v1.6"
git tag v1.6
git push --tags                            # to whichever remote(s) you use
```

`release.py` is the **only** thing that writes version numbers — never edit them
by hand. `release.py --check` verifies coherence without writing; both
`tests/test_versioning.py` and `clean_env_check.sh` run it, so an inconsistent
tree cannot pass the gates.

### Git-host independence

Nothing in the build system names a git host. Pins are plain package names and
versions; you clone this repository from GitHub, GitLab, Gitea or a USB stick,
check out the tag and build. If your checkout is not at the pinned tag the build
is rejected and told which tag to check out — it never tries to fetch it for you.

### How a version mismatch is caught — three independent layers

1. **Build time** (the host's `deploy.py`, via `toto.versioning`). Before
   building, `verify_checkout()` reads the host's `requirements.toto.txt` and
   checks the `toto_libs` checkout: `VERSION` must equal the pin and — unless
   `--dev` — the checkout must sit on the release tag with a clean tree. That
   catches the everyday mistake (three commits into a feature branch, `VERSION`
   still reading the last release, deploying out of habit). After building,
   `verify_wheels()` compares every wheel's filename version to the manifest, so
   a stale wheel in `dist/` cannot slip through.
2. **Install time** (pip). Each package pins its siblings exactly, so pip itself
   refuses a mixed suite; `--no-index --find-links` resolves those pins against
   the staged wheels alone.
3. **Runtime** (`toto.core` AppConfig). `check_runtime_coherence()` refuses to
   boot a mixed-version installation, or one where the pre-split `toto`
   distribution still shadows the namespace. `TOTO_SKIP_VERSION_CHECK=1`
   bypasses it locally.

### Keeping the partition honest

`scripts/check_package_graph.py` re-derives the truth on every run: membership
from the filesystem (`packages/<dist>/src/toto/<portion>`), the dependency graph
from the pyprojects. It fails if a module belongs to no package or to two, if
the graph gains a cycle, if any *hard* edge crosses an undeclared boundary, or
if versions and sibling pins disagree. It runs inside `pytest` and in
`clean_env_check.sh`. When it reports `UNDECLARED DEPENDENCY toto-x -> toto-y`,
there are three honest fixes, in order of preference:

1. make the import lazy (move it into the function that uses it) — when the
   integration really is optional;
2. add `toto-y` to `toto-x`'s `dependencies` — when the need is real and the
   direction creates no cycle;
3. move the app to the other package — when the boundary was wrong.

### The gate catalogue

| Gate | Command | Proves |
|---|---|---|
| partition | `scripts/check_package_graph.py` | membership, acyclic DAG, no undeclared hard edge |
| versions | `scripts/release.py --check` | VERSION == every package version == every sibling pin |
| clean env | `scripts/clean_env_check.sh` | sdists build; wheels build *from* the sdists (MANIFEST.in proof); install offline; the payload is complete, disjoint and unchanged; Django checks pass against the installed wheels (with the source tree unable to shadow them); and each dependency tier stands alone |

The tier matrix in `clean_env_check.sh` installs each dependency tier by itself
(`toto-base`, then each package that only needs base, up to the full graph
stack): a hidden import from a lower tier into a higher one fails there and
nowhere else. Repository layout for reference:

```
toto_libs/
  VERSION                     the suite version — single source of truth
  packages/<dist>/
    pyproject.toml            static version + exact sibling pins
    src/toto/<app>/           namespace portion (never a toto/__init__.py)
  scripts/                    release, build, install, partition checker, gates
  tests/                      packaging/versioning/tier gates (shipped in no wheel)
  limbo/                      parked apps, outside every package
```

### Development workflows

- **Editable everywhere:** develop with `scripts/install_toto.sh`; hosts stay on
  their pinned tag.
- **Unreleased code in a host stack:** `./deploy_local.sh --dev` (local only;
  a `--dev` build may never be pushed).
- **Deploy mid-feature:** keep the pinned tag in a `git worktree` and point the
  host at it via `TOTO_SRC`/`toto_src:`.

### Error catalogue

| Message | Meaning | Fix |
|---|---|---|
| `toto_libs checkout … is at version 1.5, this host requires 1.6` | checkout is not the pinned release | `git -C <checkout> checkout v1.6`, or `--dev` for a local build |
| `toto_libs checkout … is not on tag v1.6 (HEAD is …)` | right VERSION file, wrong commit (feature branch) | `git -C <checkout> checkout v1.6`, or `--dev` |
| `toto_libs checkout … has N uncommitted change(s)` | the build would not reproduce the tag | commit/stash, or `--dev` |
| `… is not a toto suite checkout; this looks like a pre-split checkout` | checkout predates v1.0 | check out a v1.x tag |
| `built the wrong version: toto-base 1.5 (this host requires 1.6)` | stale or mis-built wheels | check out the tag and rebuild |
| `missing wheels for toto-chat` | manifest pins a package that was not built | rebuild; confirm the package exists in this release |
| `unexpected toto wheels staged: toto-graph` | leftovers in `dist/` | clear `dist/` and rebuild, or add the package to the manifest |
| `… mixes versions (…)` | manifest pins differ | pin every package to the same version |
| `'toto-base>=1.6' is not an exact toto pin` | ranges/URLs in a manifest | use `toto-<package>==<major>.<release>` |
| `the pre-split 'toto' distribution … is still installed` | old wheel shadows the namespace | `pip uninstall -y toto` and reinstall |
| `the installed toto suite is incoherent: …` | half-upgraded venv | reinstall every package from one release |
| `--dev builds may never be pushed` | `--dev` on a server deploy | check out the pinned tag and rebuild |

### Troubleshooting

- **`import toto` works but an app is missing.** The host pinned fewer packages
  than its `INSTALLED_APPS` needs — add the package to `requirements.toto.txt`
  (app→package mapping is the table in *How it works*).
- **A host image reuses a stale library.** `deploy.py --smart` reuses an image
  while its fingerprint (staged wheels + `requirements*.txt`) is unchanged, so a
  version change always rebuilds; force one with `BUILD=1 ./deploy_local.sh`.
- **GDAL/spatialite errors in the gates.** Use the system interpreter:
  `PYTHON=/usr/bin/python3 scripts/clean_env_check.sh`.
- **Editable installs behave oddly after moving apps between packages.** Remove
  and reinstall in one transaction (`pip uninstall -y toto-base … &&
  scripts/install_toto.sh`); stale `.egg-info`/`__editable__` finders from a
  previous layout are the usual cause.

---

## Architecture history

toto began as one product's monorepo and became a shared library. Its shape
today is the result of two forces: a dependency graph that refuses to be sliced
arbitrarily, and a growing set of hosts that each want a different subset. Two
mechanisms keep both under control — **library packages** (lockstep pip
distributions, for what more than one host needs) and **host-carried namespace
portions** (plain source, for what only one host needs).

### Why the packages are shaped the way they are

The boundaries come from the hard/soft edge rule, not taxonomy. `toto-base` is
big because `core`, `gervazy`, `people`, `locations` and `events` form one
irreducible foreign-key/import cycle that every host installs anyway — so
`locations` in particular is fused into base and cannot be pulled out. Where a
cluster *is* separable it became its own package: `toto-media` (v1.5) extracted
the ffmpeg/Whisper stack so only media-capable hosts carry it, and `toto-geo`
(v1.6) extracted `weather`. A crucial finding made all of this cheap: **no
ForeignKey, M2M, one-to-one or migration dependency anywhere in the suite points
at a movable app** — the only cross-app model relations target `people.Person`
and `vault.VaultFile`, so every other coupling is (mostly guarded) Python-import
coupling.

### Host-carried namespace portions

An app that only one host needs does **not** become a new pip package and does
**not** change its import path. The host repo simply carries `toto/<app>/` as
plain source at its repo root (no `__init__.py`), and PEP 420 merges it with the
installed wheels — verified experimentally: with `toto-base` installed and a
source dir on the path, `import toto.registry` and `import toto.<portion-app>`
both resolve. Nothing else changes: app labels, migrations, `INSTALLED_APPS`
strings, `apps.is_installed(...)`, `reverse(...)`, templates and static all
behave identically, and the runtime version gate never sees the portion because
it has no distribution metadata. If the portion is ever off `sys.path`, Django
fails loudly with `No installed app with label '<app>'`.

### The wave record

- **v1.0 — the split.** The original library was cut into the multi-package
  `toto.*` namespace suite; import paths, app labels and migration history were
  preserved unchanged.
- **v1.3 — first host secessions.** Tor infrastructure `aster` and `nomad`
  seceded to **faros**; `notarius`, `polls`, `travels`, `sketch` seceded to
  **zenobia**. Each host began carrying its own source portion; `toto-ops` was
  left holding only `monit`, a legitimately shared one-app package. Measured, not
  estimated: migration-apps 43→37, templates 245→208. (A packaging trap surfaced
  here: setuptools reused `build/lib/` and re-shipped deleted apps — hence
  `build_wheels.py` now clears `build/` per package.)
- **v1.4 — git and tex.** `gitvault` and `texlab` seceded to zenobia;
  `texplay`, installed by no host and registered by nothing, was parked in
  `limbo/`. faros pinned no `toto-works` yet still bumped 1.3→1.4 — the lockstep
  rule means every host tracks every release. This was also the first time
  `toto-base` kept guarded, function-level references into a now host-owned app.
- **v1.5 — `toto-media`.** A *repackaging*, not a secession: `manta`,
  `transcription`, `vod` and `fileservices` were grouped into a new library
  package gated by `BUILD_MEDIA`; the apps stayed in the library and only
  media-capable hosts pin it.
- **v1.6 — `toto-geo` and a base slim-down.** `weather` moved into a new
  `toto-geo` package; `kanban` and `memo` moved from `toto-base` down to
  `toto-works`. Another repackaging. The library is now **38 apps across 9
  packages**, and both current hosts' clean-env gates pass with their own apps
  loading from their own repos.
- **v1.7 — GIS made optional (`BUILD_GEO`).** Not a repackaging: `toto.locations`
  gained a geometry-less mode so a host can drop the entire GDAL/GEOS/PROJ/PostGIS
  stack. `Address` gained canonical lat/lon floats (migration `0005`), a second
  hand-maintained migration graph `migrations_nogis/` builds the seven tables
  without geometry columns, `core.base_admin` no longer hard-imports `OSMGeoAdmin`,
  and `resolve_features` rejects `BUILD_WEATHER`/`BUILD_TRAVELS` under
  `BUILD_GEO=0`. faros ships a `BUILD_GEO=0` light onion profile; zenobia stays
  GIS-on. See "Making GIS optional" above.

The current split: **zenobia** pins all ten packages and carries
`zenobia/toto/{notarius,polls,sketch,travels,texlab,gitvault}`; **faros** pins
`toto-base`, `toto-auth`, `toto-flow`, `toto-chat`, `toto-ops` and carries
`faros/toto/{aster,nomad}`.

### The rule for leaving, and staying

An app may leave the library only when **all three** hold: exactly one current
host installs it; no *planned* host plausibly needs it (delta and aurelian
included — the expensive test to get wrong); and no library code hard-depends on
it. Several apps are zenobia-only today but stay because a planned host has a
concrete claim: `memo`, `vod` and `transcription` (delta's video lessons with
subtitles); `kanban` and the whole `toto-graph` cluster (aurelian is
Neo4j-central and economy-heavy); `verbena` cannot leave at all because
`socialhub`, installed everywhere, imports it at module level.

### Future-host outlook

- **delta (e-learning).** Revives `academy` (Course → Module → Lesson, each
  Lesson backed by a `memo.MemoDeck`), `quizzes`, `library` and `palimpsest`
  from `limbo/` as delta's own portion (or a `toto-learn` package if a second
  host ever wants them). It pins `memo`, `vod` and `transcription` today, which
  is exactly why those stay shared.
- **aurelian (fleet + economy + governance).** A large, tightly self-referential
  revival (ledger → instruments → billing → peer economy → commerce → governance
  → operations) that pins `kanban`, `locations`, `events`, `quota`, `vault`,
  `workflows` and the entire graph cluster — the substantive reason `toto-graph`
  stays in the library rather than following zenobia. Its portion is large enough
  that it may eventually want its own packaging; per the namespace rule, such a
  host-owned distribution must **not** be named `toto-*` (or the coherence guard
  would demand it match the suite version), and the preferred path is promotion
  into the library once a second host wants it.

Two `limbo/` apps are already earmarked to return as **shared library** apps
because both future hosts need them: `competence` (academy awards skill badges;
aurelian's mobilization matches responder skills against them) and
`subscriptions` (gates academy courses, vod collections and aurelian's recurring
revenue alike). The converging shape: the library keeps what more than one host
needs — the platform core, the graph cluster, the cross-cutting infrastructure —
and each host owns its idiosyncrasies as source portions, promoting an app into
the library only when a *second* host needs it.
