# Building, versioning and pinning the toto suite

This is the manual for the toto library's build system. It exists to make one
thing true: **you can develop toto freely and deploy a host without fear**,
because a host only ever builds against the exact library version it declares,
and every layer of the toolchain refuses anything else.

---

## 1. Concepts

**One repository, several distributions.** toto ships as nine pip packages
that all fill the same `toto.*` import namespace (a PEP 420 namespace package —
there is deliberately no `toto/__init__.py` anywhere):

| Package | Contains | Depends on |
|---|---|---|
| `toto-base` | the shared host API (`features`, `registry`, `routing`, `schedules`, `conf`, `celery_utils`, `versioning`), `ui`, `ingress`, and the apps every host installs (`registry.BASE_APPS`): core, api, backup, gervazy, vault, people, locations, socialhub, events, verbena, quota, sso_core, sso_master — plus `editor` and `sso_client`, which are host-selected rather than unconditional | — |
| `toto-flow` | workflows, mandragora | base |
| `toto-works` | antaresia, kanban, memo | base, flow |
| `toto-geo` | weather (observations/forecasts keyed off `locations.Address`) | base, flow |
| `toto-media` | manta, transcription, vod, fileservices — the video/media stack (`BUILD_MEDIA`) | base, flow |
| `toto-chat` | forum | base |
| `toto-ops` | monit | base |
| `toto-ai` | sabbia, steven, vicuna | base |
| `toto-graph` | ravioli, sql_neo4j_sync, neo_editor, bento, ingestor, connectors, formica, ocr | base, flow, ai |

Import paths and Django app labels are **unchanged** by the split: `toto.vault`
is still `toto.vault`, migrations and their app labels are untouched.

**Why these boundaries.** They come from the dependency graph, not from
taxonomy. Two rules decide everything:

- A *hard* edge — a module-level import, a `ForeignKey("app.Model")`, a
  migration dependency, or an `AppConfig.ready()` guard — means the target must
  be installed, so it must sit in the same package or one below it.
- A *soft* edge — an import inside a function, under `try/except ImportError`,
  or in an autodiscovered plugin (`<app>/plugins/*_plugins.py`,
  `<app>/predefined_tasks.py`) — is an optional integration and must **never**
  become an install dependency.

`toto-base` is large because it has to be: `gervazy.PersonSigningKey` has a
foreign key to `people.Person`, `people` imports `toto.core`, and
`locations`/`events` import each other. That is one irreducible cycle, and
every host installs all of it anyway.

**Lockstep versioning.** Versions are `MAJOR.RELEASE` (two integers, e.g.
`1.4`). One repository tag `vX.Y` releases the whole suite: every package is
built at exactly `X.Y` and pins its siblings to exactly `X.Y`. So "check out
`v1.4`" is a complete, unambiguous instruction — there is no per-package
version matrix to reason about.

- Bump **MAJOR** when hosts must change something: a settings contract, a host
  API signature, an app label, a migration that is not backwards compatible.
- Bump **RELEASE** for everything else, including fixes.

**Git-host independence.** Nothing in the build system names a git host. Pins
are plain package names and versions; you clone this repository from GitHub,
GitLab, Gitea or a USB stick, check out the tag, and build. If your checkout is
not at the pinned tag, the build is rejected and told you which tag to check
out — it never tries to fetch it for you.

---

## 2. Repository layout

```
toto_libs/
  VERSION                     the suite version — the single source of truth
  packages/<dist>/
    pyproject.toml            static version + exact sibling pins
    MANIFEST.in
    src/toto/<app>/           namespace portion (never a toto/__init__.py)
  tests/                      packaging, versioning and tier gates (shipped in no wheel)
  scripts/                    release, build, install, partition checker, gates
  limbo/                      parked apps, outside every package
```

---

## 3. Cutting a release

```bash
git status --porcelain                 # must be empty
PYTHON=/usr/bin/python3 scripts/clean_env_check.sh
python scripts/release.py 1.4          # rewrites VERSION + 7 versions + all sibling pins
git commit -am "release v1.4"
git tag v1.4
git push --tags                        # to whichever remote(s) you use
```

`release.py` is the only thing that writes version numbers — never edit them by
hand. `scripts/release.py --check` verifies coherence without writing, and both
`tests/test_versioning.py` and `clean_env_check.sh` run it, so an inconsistent
tree cannot pass the gates.

Hosts pick a release up when *they* choose, by editing their manifest — nothing
upgrades on its own.

---

## 4. How a version mismatch is caught (three independent layers)

**Build time (the host's `deploy.py`).** Before building wheels it reads the
host's `requirements.toto.txt`, then checks the toto_libs checkout: the
`VERSION` file must equal the pin, and — unless you passed `--dev` — the
checkout must sit on the release tag with a clean working tree. That last part
is what catches the everyday mistake: you are three commits into a feature
branch, `VERSION` still reads the last release, and you deploy out of habit.
After building, every wheel's filename version is compared to the manifest
again, so a stale wheel in `dist/` cannot slip through.

**Install time (pip).** Each package pins its siblings exactly, so pip itself
refuses a mixed suite. The hosts' images install with `--no-index
--find-links`, which means pip resolves those pins against the staged wheels
alone and never reaches the network.

**Runtime (`toto.core` AppConfig).** On boot, `check_runtime_coherence()` scans
the installed `toto-*` distributions and raises `ImproperlyConfigured` if their
versions differ, or if a pre-split `toto` distribution is still installed and
shadowing the namespace. Set `TOTO_SKIP_VERSION_CHECK=1` to bypass it while
experimenting locally.

---

## 5. Building and installing without a package index

```bash
git clone <any-host>/toto_libs.git && cd toto_libs
git checkout v1.4
python scripts/build_wheels.py                  # all packages -> dist/
python scripts/build_wheels.py --only toto-base,toto-flow
python scripts/build_wheels.py --sdist          # sdist first, then wheel from it
```

The script prints the install line to use on the target machine, e.g.:

```bash
pip install --no-index --find-links dist toto-base==1.4 toto-flow==1.4
```

Install the whole suite editable for development:

```bash
scripts/install_toto.sh          # one pip call: the exact sibling pins need it
```

**Upgrading from the pre-split library (once per environment):**

```bash
pip uninstall -y toto            # the old single distribution shadows the namespace
scripts/install_toto.sh
```

---

## 6. Keeping the partition honest

`scripts/check_package_graph.py` re-derives the truth on every run: membership
from the filesystem, the dependency graph from the pyprojects. It fails if a
module belongs to no package or two, if the package graph gains a cycle, if any
*hard* edge crosses a package boundary that is not declared, or if versions and
sibling pins disagree. It runs inside `pytest` and in `clean_env_check.sh`.

When it reports `UNDECLARED DEPENDENCY toto-x -> toto-y`, you have three
honest options, in order of preference:

1. make the import lazy (move it into the function that uses it) — correct when
   the integration really is optional;
2. add `toto-y` to `toto-x`'s `dependencies` — correct when the need is real
   and the direction does not create a cycle;
3. move the app to the other package — correct when the boundary was wrong.

---

## 7. The gates

| Gate | Command | Proves |
|---|---|---|
| partition | `scripts/check_package_graph.py` | membership, acyclic DAG, no undeclared hard edge |
| versions | `scripts/release.py --check` | VERSION == every package version == every sibling pin |
| clean env | `scripts/clean_env_check.sh` | sdists build, wheels build *from* the sdists, install offline, payload complete/disjoint/unchanged, Django checks pass from the installed wheels, and each dependency tier stands alone |

`clean_env_check.sh` builds fresh venvs. `toto.locations` uses GIS fields, so
the interpreter must load the system GDAL — a conda python usually cannot, so
run it as `PYTHON=/usr/bin/python3 scripts/clean_env_check.sh`.

---

## 8. Development workflows

**Day to day.** Hack on this repository with everything installed editable
(`scripts/install_toto.sh`). Hosts keep pointing at their pinned tag, so your
work in progress cannot reach a deployment by accident.

**Testing unreleased library code in a host's docker stack.** Use the dev
escape hatch, which is allowed for local bring-up and refused for anything that
leaves the machine:

```bash
./deploy_local.sh --dev              # or: deploy.py <config> up --dev
```

**Deploying while mid-feature.** Keep the pinned tag in a second working tree
so you never have to stash:

```bash
git worktree add ../toto_libs_stable v1.4
```

then point the host at it (`toto_src: ../toto_libs_stable` in the deploy config,
or `TOTO_SRC=../toto_libs_stable`).

---

## 9. Error catalogue

| Message | Meaning | Fix |
|---|---|---|
| `toto_libs checkout … is at version 1.5, this host requires 1.4` | the checkout is not the pinned release | `git -C <checkout> checkout v1.4`, or `--dev` for a local build |
| `toto_libs checkout … is not on tag v1.4 (HEAD is …)` | right VERSION file, wrong commit — typically a feature branch | `git -C <checkout> checkout v1.4`, or `--dev` |
| `toto_libs checkout … has N uncommitted change(s)` | the build would not reproduce the tag | commit/stash, or `--dev` |
| `… is not a toto suite checkout; this looks like a pre-split checkout` | checkout predates v1.0 | check out a v1.x tag |
| `built the wrong version: toto-base 1.5 (this host requires 1.4)` | stale or mis-built wheels | check out the tag and rebuild |
| `missing wheels for toto-chat` | manifest pins a package that was not built | rebuild; check the package exists in this release |
| `unexpected toto wheels staged: toto-graph` | leftovers in `dist/` | clear `dist/` and rebuild, or add the package to the manifest |
| `… mixes versions (…)` | manifest pins differ | pin every package to the same version |
| `'toto-base>=1.4' is not an exact toto pin` | ranges/URLs in a manifest | use `toto-<package>==<major>.<release>` |
| `the pre-split 'toto' distribution 0.3.2 is still installed` | old wheel shadows the namespace | `pip uninstall -y toto` and reinstall |
| `the installed toto suite is incoherent: …` | half-upgraded venv | reinstall every package from one release |
| `--dev builds may never be pushed` | `--dev` on a server deploy | check out the pinned tag and rebuild |

---

## 10. Troubleshooting

**`import toto` works but an app is missing.** The host pinned fewer packages
than its `INSTALLED_APPS` needs. Add the package to `requirements.toto.txt`;
the app-to-package table is in §1.

**A host image reuses a stale library.** `deploy.py --smart` reuses an image
while its fingerprint is unchanged; that fingerprint covers the staged wheels
and `requirements*.txt`, so a version change always rebuilds. Force one with
`BUILD=1 ./deploy_local.sh`.

**GDAL/spatialite errors in the gates.** See §7 — use the system interpreter.

**Editable installs behave oddly after moving apps between packages.** Remove
and reinstall in one transaction: `pip uninstall -y toto-base … &&
scripts/install_toto.sh`. Stale `.egg-info`/`__editable__` finders from a
previous layout are the usual cause.
