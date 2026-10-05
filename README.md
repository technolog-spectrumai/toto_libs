# toto

**toto** is a library of Django apps. All of them live under one Python
namespace, `toto.*`, and the library is shipped as sixteen pip packages
(`toto-base`, `toto-auth`, `toto-flow`, …) that each fill a part of that
namespace. The library is never run by itself: a **host project** supplies the
Django settings, the URL tree, the server entry point and the deployment, pins
the packages it wants, and installs the apps it wants out of them.

This file is the manual for the whole repository. It was rewritten on
2026-10-06 from the code as it stood at library commit `650d6f32` on the
branch `dev_django5`, at suite version **2.0**. Every statement below was
checked against the source tree; where something could not be checked from
this repository, the text says so. Each package also has a README of its own
under `packages/<name>/README.md`; several of those are older than this file
and still describe earlier layouts (for example they count nine or ten
packages), so when the two disagree, trust the code first and this file
second.

## Contents

1. [What the library is](#1-what-the-library-is)
2. [Who uses it: the hosts](#2-who-uses-it-the-hosts)
3. [Repository layout](#3-repository-layout)
4. [The package graph](#4-the-package-graph)
5. [The packages and their apps](#5-the-packages-and-their-apps)
6. [Mechanisms a host must know](#6-mechanisms-a-host-must-know)
7. [Working on the library](#7-working-on-the-library)
8. [Recent changes: October 2026](#8-recent-changes-october-2026)
9. [What this file does not vouch for](#9-what-this-file-does-not-vouch-for)

---

## 1. What the library is

### One namespace, sixteen packages

`toto` is a PEP 420 namespace package. There is no `toto/__init__.py`
anywhere: not in any package's `src/`, not in a built wheel, and not in an
installed `site-packages/toto/`. Each package owns a disjoint set of members
of the namespace under `packages/<package>/src/toto/`, and Python merges them
at import time. `import toto.vault` therefore works the same whether one
wheel or six are installed, and an app keeps its import path, its Django app
label, its migrations and its `INSTALLED_APPS` string when it moves from one
package to another.

At version 2.0 the tree holds:

| What | How many | Where |
|---|---|---|
| Packages (pip distributions) | 16 | `packages/*/pyproject.toml` |
| Django apps (directories with an `apps.py`) | 60 | `packages/*/src/toto/<app>/` |
| Namespace members in total | 72 | the 60 apps, the two non-app packages `toto.ui` and `toto.ingress`, and ten single modules |
| Parked apps, in no package | 3 | `limbo/datalink`, `limbo/hesperis`, `limbo/polls` |

The ten single modules are the host API. Eight are in `toto-base`
(`toto.features`, `toto.registry`, `toto.schedules`, `toto.routing`,
`toto.conf`, `toto.celery_utils`, `toto.versioning`, `toto.tests_schedules`)
and two are in `toto-auth` (`toto.auth_config`, `toto.auth_local_urls`).
`scripts/check_package_graph.py` counts these 72 members and 16 packages on
every run.

### Lockstep versions

Every package carries the same version, and every dependency of one toto
package on another is an exact pin on that version (`"toto-base==2.0"`). The
number is `MAJOR.RELEASE`, two integers, and the file `VERSION` at the
repository root is its single source. A host therefore pins a set of packages
at one version, never a matrix of versions.

There is deliberately no `toto.__version__`: `toto` is a namespace shared by
several distributions, so the version is read from distribution metadata
(`importlib.metadata.version("toto-base")`).

### What a host gets

Depending on the apps it installs, a host gets: accounts and sign-in with an
OpenID Connect provider or consumer; people, communities, membership
applications and clearances; events and availability; file storage in buckets
with sharing, trash, versions and remote backends; an encryption layer with
per-user key hierarchies and document signing; a hash-chained audit trail;
usage metering with limits, prices, a double-entry ledger, mana pools and
subscription plans; a workflow engine on Celery; notifications with a bell in
the app bar; an operations dashboard with alerts; and, in packages the main
host does not install today, a forum, maps and weather, a Neo4j knowledge
graph, assistants, project boards, a document writer, slide decks, local git,
media processing and booked compute.

---

## 2. Who uses it: the hosts

### zenobia, the main host

zenobia is the host this library is developed against. Its repository (the
"monorepo", on the owner's machine at
`/home/janek/Desktop/dev/zenobia/zenobia`) carries a **byte-identical copy**
of this repository at `vendor/toto_libs/` and builds its wheels from that
copy. The copy includes everything here, `limbo/` and the packages zenobia
does not install included, because this library's own tests assert
whole-suite counts.

zenobia pins **five of the sixteen packages**, all at 2.0, in
`zenobia/requirements.toto.txt`:

```
toto-base==2.0
toto-auth==2.0
toto-flow==2.0
toto-ops==2.0
toto-economy==2.0
```

The other eleven are named in that file as deliberately absent: `toto-graph`,
`toto-ai`, `toto-anastasia`, `toto-media-ops`, `toto-media`, `toto-business`,
`toto-works`, `toto-ambrosia`, `toto-repo`, `toto-chat` and `toto-geo`. Their
wheels are not built for zenobia and do not enter its image.

Out of the five pinned packages zenobia installs these library apps today
(`zenobia/zenobia/zenobia/settings.py`, `INSTALLED_APPS`):

| Package | Apps zenobia installs | Apps in the package zenobia does not install |
|---|---|---|
| `toto-base` | `toto.core`, `toto.api`, `toto.audit`, `toto.gervazy`, `toto.vault`, `toto.people`, `toto.socialhub`, `toto.events`, `toto.verbena`, `toto.quota`, `toto.notify`, `toto.subscriptions` | `toto.antivirus`, `toto.comments`, `toto.editor`, `toto.jess`, `toto.mail` |
| `toto-auth` | `toto.sso_core`, `toto.sso_master`, `toto.social_login` (the provider block of `toto.auth_config.auth_apps`) | `toto.sso_client` |
| `toto-flow` | `toto.workflows` | `toto.mandragora` |
| `toto-ops` | `toto.monit`, when `BUILD_MONIT=1`; every profile shipped in the monorepo sets it | — |
| `toto-economy` | `toto.assets`, `toto.tariffs`, `toto.mint`, `toto.tax`, `toto.mana` | `toto.clearing` |

That is 22 library apps with monitoring on. zenobia also carries apps of its
own as a PEP 420 portion of the same namespace (`zenobia/zenobia/toto/`; its
settings install `toto.yamabiko`, `toto.operator` and, under `BUILD_BOURSE`,
`toto.bourse`). Those are not part of this library and are not described
here.

What zenobia is today shapes what is exercised in this library. It is a file
storage platform: it sets `VAULT_STORAGE_ONLY = True`, runs as the identity
provider only, installs no map, no forum, no editors, no antivirus and no PDF
renderer, and opens no WebSocket and holds no request open. The apps and
packages it does not install are still built, version-bumped and checked by
this repository's gates, but nothing deploys them.

### Other hosts

This repository does not contain any host, and it cannot tell which hosts
still build against it. What the repository itself shows:

| Host | What this repository shows about it |
|---|---|
| **faros** | `scripts/make_faros_vendor.sh` builds a `faros_vendor` branch from a `faros` branch, leaving out seven packages. Code comments call it a Tor host that sets `VAULT_FILE_EDITS = False` and `VAULT_EXTERNAL_BUCKETS = False`, and `toto.core` and `toto.monit` keep `apps.is_installed` guards for `toto.nomad` and `toto.aster`, two apps that are not in this tree. No branch named `faros` exists any more; `legacy/faros` and `legacy/faros_vendor` do. |
| **aurelian** | `scripts/make_aurelian_vendor.sh` does the same for an `aurelian` branch. `toto/quota/charge.py` names it as the host that pins no economy package and must still run every metered endpoint for free. Only `legacy/aurelian` and `legacy/aurelian_vendor` exist as branches. |
| **placidia** | No script. The `toto-repo` README describes it as a federation consumer (`TOTO_AUTH_MODE=consumer`) that owned the workspace labs, and `toto/features.py` says it is being dismantled. A `legacy/placidia_vendor` branch exists. |
| **poseidon** | A `legacy/poseidon_vendor` branch exists. Otherwise the name appears only as a peer name in one vault test. |
| **emilia** | No branch and no script. The name appears only as a peer name in the same vault test. |
| **delta** | A `legacy/delta` branch exists. Comments in `toto.subscriptions` and `toto.antivirus` name it as the host whose apps some rules here were taken from. |

Two things follow. First, both vendor scripts for other hosts name branches
that no longer exist and exclusion lists written when the suite had fewer
packages, so they are records of an older arrangement and will not run as
they stand. Second, the library changes of September and October 2026 (see
section 8) were made for zenobia; version 2.0 is a major release precisely
because other hosts must act to follow it. Treat every other host as possibly
behind or broken until its own repository says otherwise.

### Host-carried portions

An app that only one host needs does not have to be in this library. A host
can carry `toto/<app>/` as plain source in its own repository, with no
`__init__.py` in the `toto/` directory, and PEP 420 merges it with the
installed wheels. App labels, migrations, `reverse()` names and
`apps.is_installed(...)` behave exactly as for a library app, and the runtime
version check does not see the portion because it has no distribution
metadata. This is why `toto.features` resolves flags for apps that are in no
package here (`canasta`, `travels`, `texlab`, `dracena`) while
`toto.registry.FEATURE_APPS` has no entry for them: the flag is part of the
host contract, the `INSTALLED_APPS` line is the host's own.

---

## 3. Repository layout

```
toto_libs/
  VERSION                 the suite version, "2.0" — the single source
  README.md               this file
  LICENSE                 MIT
  pyproject.toml          NOT a distribution: only pytest's testpaths
  MANIFEST.in             left from the single-distribution layout; the root is not built
  packages/
    <package>/
      pyproject.toml      name, version, exact pins on sibling packages
      README.md           the package's own manual
      MANIFEST.in         what an sdist carries besides Python files
      src/toto/<app>/     the apps (never a src/toto/__init__.py)
  scripts/                the package graph check, release, build, gates
  tests/                  the library's own harness (ships in no wheel)
  limbo/                  parked apps, each with a PARKED.md; in no package
```

`dist/`, `build/`, `.venv_test/` and `.pytest_cache/` are build and test
output and are git-ignored. So is
`packages/toto-base/src/toto/core/static/oya/tailwind.css`: a host builds
that stylesheet from the classes its installed apps use, and the library
falls back to a script when it is absent.

The scripts, by what they are for:

| Script | What it does |
|---|---|
| `scripts/check_package_graph.py` | Derives which package owns which namespace member from the filesystem and the dependency graph from the `pyproject.toml` files, then fails on an unowned or doubly owned member, a cycle, a hard dependency that crosses an undeclared package boundary, or a version that is out of step. See section 7. |
| `scripts/release.py` | Writes the suite version into `VERSION`, every `pyproject.toml` and every sibling pin; `--check` verifies and writes nothing. |
| `scripts/build_wheels.py` | Builds wheels (and with `--sdist`, sdists first and the wheels from them) into `dist/`, clearing each package's stale `build/` tree first. |
| `scripts/install_toto.sh` | Installs all sixteen packages editable in one pip call, which is the only way pip can satisfy their exact pins on each other. |
| `scripts/clean_env_check.sh` | The clean-environment gate. See section 7. |
| `scripts/make_zenobia_vendor.sh`, `make_faros_vendor.sh`, `make_aurelian_vendor.sh` | Built per-host `*_vendor` branches for `git subtree` vendoring. They name source branches that no longer exist; zenobia is re-vendored by rsync today (section 7). |
| `scripts/clean_migrations.sh`, `reset.sh`, `dev_side.sh`, `nginx.conf.j2` | Older helpers: deleting migration directories, a container entry point that migrates and seeds, a local Redis and Celery starter, and an nginx template. Nothing else in this repository calls them. |

---

## 4. The package graph

The table lists each package's direct dependencies on other toto packages,
as declared in its `pyproject.toml`. Every one is an exact pin at 2.0.

| Package | Depends on | Apps | zenobia pins it |
|---|---|---|---|
| `toto-base` | — (`Django>=4.2,<6`, `PyYAML>=6.0`) | 17 | yes |
| `toto-auth` | base | 4 | yes |
| `toto-flow` | base | 2 | yes |
| `toto-economy` | base | 6 | yes |
| `toto-ops` | base | 1 | yes |
| `toto-chat` | base | 1 | no |
| `toto-ai` | base | 3 | no |
| `toto-ambrosia` | base | 1 | no |
| `toto-anastasia` | base | 1 | no |
| `toto-media` | base | 2 | no |
| `toto-geo` | base, flow | 2 | no |
| `toto-repo` | base, flow | 2 | no |
| `toto-media-ops` | base, flow | 3 | no |
| `toto-works` | base, geo | 4 | no |
| `toto-business` | base, geo | 4 | no |
| `toto-graph` | base, flow, ai | 7 | no |

Read as layers: `toto-base` is the foundation and depends on no sibling.
Nine packages need only base. `toto-geo`, `toto-repo` and `toto-media-ops`
need the workflow engine as well, because each has a model with a foreign key
to `workflows.WorkflowRun`. `toto-works` and `toto-business` need `toto-geo`
since 2026-10-04, because `toto.kanban` and `toto.company` have foreign keys
to map models (`locations.Zone`, `locations.Address`, `locations.Route`) and
the map left `toto-base` that day. `toto-graph` sits on `toto-ai`.

### Hard and soft edges

Which package an app lives in is decided by one rule, and
`scripts/check_package_graph.py` enforces it:

- A **hard** edge means the target must be installed: an import at module
  level, a `ForeignKey("app.Model")` string, a migration dependency, or an
  `AppConfig.ready()` that refuses to start without another app. The target
  must be in the same package or in one the package declares.
- A **soft** edge is an optional integration: an import inside a function,
  under `try/except ImportError`, behind `apps.is_installed(...)`, or in a
  module that only another app's autodiscovery imports
  (`<app>/plugins/*_plugins.py`, `<app>/predefined_tasks.py`). A soft edge
  must never become an install dependency, and the check ignores it on
  purpose. Test files may import anything.

Two patterns recur because of this rule. The first is the lazy façade:
`toto.quota.charge`, `toto.quota.rates`, `toto.quota.times` and
`toto.quota.levies` let apps in `toto-base` reach prices, time grants and
levies in `toto-economy` without importing it, and answer "free" or "nothing"
where it is absent. The second is the slug instead of a foreign key:
`socialhub.CommunityForum` and `company.CompanyForum` keep the *slug* of a
forum room, resolved when the page is drawn, because a foreign key string to
`forum.ForumChannel` would be a hard edge into `toto-chat`.

---

## 5. The packages and their apps

Each section says what the package is for, what it depends on, and gives
every app in it: what the app does, its main models and doors, what it uses
only when present, and whether zenobia installs it today. "Doors" are the URL
namespace an app mounts and its notable entry points. The guards listed as
"optional" are the `apps.is_installed("toto.…")` checks found in the app's
non-test code.

### 5.1 toto-base

The foundation, and the one package every host installs. It depends on no
other toto package. It carries the host API modules described in section 6
and seventeen apps. Extras: `toto-base[s3]` adds `boto3` for S3 buckets and
`toto-base[remote-vault]` adds `requests`.

Since 2026-10-04 `toto-base` contains no geography: it needs no GIS library
and installs without GDAL. Addresses on a person, an event and a community
are text.

| App | Tables | zenobia |
|---|---|---|
| `toto.core` | yes | yes |
| `toto.api` | yes | yes |
| `toto.audit` | yes | yes |
| `toto.gervazy` | yes | yes |
| `toto.vault` | yes | yes |
| `toto.people` | yes | yes |
| `toto.socialhub` | yes | yes |
| `toto.events` | yes | yes |
| `toto.verbena` | abstract models only | yes |
| `toto.quota` | abstract models only | yes |
| `toto.notify` | yes | yes |
| `toto.subscriptions` | yes | yes |
| `toto.antivirus` | yes | no |
| `toto.comments` | yes | no |
| `toto.editor` | none | no |
| `toto.jess` | yes | no |
| `toto.mail` | yes | no |

**`toto.core`** is the first app to load and the platform itself. Its models
are `Platform` (the one row that names this deployment), `Federation`, the
theme (`Theme`, `ColorMix`, `Font`), `UserSession` and `KnownSignIn` (a
member's sign-ins), `NoticeDelivery` (how account mails fared) and
`BootstrapMarker`. It owns the base templates and the app bar, the dashboard,
the sign-in form and its lockout (`signin_lockout.py`), the client address
rule (`client_ip.py`), the middleware listed in section 6, the plugin base
classes, the account notices, the personal-data export and the erasure
helpers, the nightly housekeeping task, and the console commands
`init_platform`, `ingress_all`, `export_user`, `erase_user` and
`unlock_signin`. Its `AppConfig.ready()` runs the suite's version check, so
a mixed installation does not boot. It guards on many optional apps,
including `toto.audit`, `toto.socialhub`, `toto.vault`, `toto.forum`,
`toto.locations`, `toto.assets` and `toto.subscriptions`. zenobia installs
it.

**`toto.api`** is the JSON surface for clients that are not the web pages.
It mounts `health/`, `apps/`, `login/`, `logout/`, `me/` and `me/mesh/`
(namespace `api`), carries the CORS rule for exact client origins
(`cors.py`), the Fetch-Metadata guard that refuses cross-site writes
(`fetch_metadata.py`), bearer tokens that are session keys (`tokens.py`) and
`TokenAuthMiddleware` for WebSockets. Its models `ApiConnector` (abstract)
and `Connector` describe outbound integrations whose secrets are kept in
gervazy. It uses `toto.audit`, `toto.forum` and `toto.vault` when present.
zenobia installs it.

**`toto.audit`** is a hash-chained, append-only record of what happened.
`AuditChain` and `AuditRecord` are its models; `toto.audit.record`, `change`
and `event` append, `verify_chain` and the `verify_audit` command check the
chain, and the pages under namespace `audit` list, show and verify records.
`AuditContextMiddleware` binds the request's user and `FileAuditMiddleware`
records vault file operations. It is unsigned: it answers whether this
platform's own record of its own edits was tampered with. zenobia installs
it, before `toto.vault`.

**`toto.gervazy`** is encryption at rest and signing. A password-derived key
unwraps a per-user `VaultMasterKey` inside a `UserStrongbox`; that wraps
`WrappedDataKey`s, which encrypt `EncryptedSecret`, `EncryptedFile` (with
`EncryptedFileChunk`) and `EncryptedPrivateKey` rows. `PersonSigningKey`
holds a person's signing identity and `CryptoAuditLog` records operations.
Its pages (namespace `gervazy`) are *My keys*, strongbox initialisation and
signing-key provisioning, and the `gen_ssl_cert` command makes a
self-signed certificate. Other apps store their secrets here. zenobia
installs it.

**`toto.vault`** is file storage: `Bucket`, `VaultDirectory`, `VaultFile`,
upload gateways (`FileGateway`), versions (`FileVersion`, `VersionBlob`),
editing locks (`FileLock`), a trash with a retention period, bucket
clearances (`BucketClearance`), storage providers and backends (this server,
S3-compatible stores, another toto host through `BucketGrant` and
`BucketPeer`), transfers between buckets, zip archives and encryption of
files. Namespace `vault`: the file pages, Management, Trash, a JSON API
under `api/`, and a peer API. Section 6 describes its access functions, its
plugin registries and the storage-only switch. It works without, and uses
when present, `toto.antivirus`, `toto.audit`, `toto.subscriptions`,
`toto.tariffs`, `toto.workflows`, `toto.people` and the apps that register
viewers and editors. Microsoft Office files are refused at every door, on
every host. zenobia installs it, with `VAULT_STORAGE_ONLY = True`.

**`toto.people`** has one model, `Person`, linked one-to-one to an optional
Django user. It is the identity most other apps point at: it holds the
display name, avatar, contact details, the address as text with the switches
`show_email`, `show_phone` and `show_address`, the memberships
(`communities`), the clearances held (`clearances`), language and time zone.
It has no pages of its own; the profile pages are socialhub's. zenobia
installs it.

**`toto.socialhub`** is communities and membership, and it is also where a
member's own account lives. Models: `Community` (whose seat is text),
`Clearance`, `CommunityPrivilege`, community news (`CommunityNewsTopic`,
`CommunityNewsPost`), `MembershipApplication` and `ReferenceRequest`,
`CommunityForum`, `PendingEmailChange`, `PrivacyNotice` and
`PrivacyAcceptance`, `DataExport` and `ErasureRequest`. Namespace
`socialhub` serves communities, profiles in tabs, applications, references
and the privacy notice; `account_urls.py` serves the account doors
(password, e-mail change, key store, data export, erasure request,
sessions). It defines the clearance rule and three plugin registries
(section 6), and its task `build_data_export` builds *Download my data*. It
uses `toto.audit`, `toto.assets`, `toto.forum`, `toto.gervazy` and
`toto.subscriptions` when present; the forum is named on a community page
only where `toto.forum` is installed. zenobia installs it.

**`toto.events`** is scheduling. `ScheduledEvent` (built on the abstract
`EventBase`, with an `EventCategory`) has an owner, organisers, a time
window and a place as text; `EventInvite` records invitations and answers;
`Availability` records when a person is free or busy. Namespace `events`
serves a calendar, event pages, invitations and a JSON API. It contributes a
section to the profile through a profile plugin. zenobia installs it.

**`toto.verbena`** provides content primitives and no tables: the abstract
models `AbstractTag`, `AbstractPage` and `AbstractSection`, slug helpers,
and a rich-text widget that wraps the third-party `trix_editor` package
with an upload script of its own. `toto.socialhub` imports it at module
level for community news, which is why it is a core app. zenobia installs
it.

**`toto.quota`** is metering and limits. It owns no tables: an app that
meters something declares its own concrete pair from `AbstractQuotaPolicy`
and `AbstractUsageEvent`. It holds the registries for metrics, time limits,
stuck-run sweeps and fee sources, the billing gateway `toto.quota.charge`,
and the pages under namespace `quota` (limits, a member's own usage, prices
and fees). Its task `sweep_stuck_runs` closes run records whose worker died.
It reaches `toto.tariffs`, `toto.tax`, `toto.mana` and `toto.subscriptions`
only when they are installed. zenobia installs it.

**`toto.notify`** is new since 2026-10-04: notifications kept per member
and the bell in the app bar. One model, `Notification`; three doors under
namespace `notify` (`api/`, `api/read/`, `api/read-all/`); one task,
`prune_read`. It writes a row when somebody else uploads, replaces, trashes
or restores a file in a bucket the recipient owns. No door holds a request
open and there is no socket: see section 6. It needs `toto.vault` to have
anything to report and uses `toto.people` for names. zenobia installs it
unconditionally.

**`toto.subscriptions`** is plans and what they allow. Plans are read from
a YAML file into immutable objects, never database rows; the models are
`Subscription`, `SubscriptionCharge`, `CommunityDiscount`,
`CommunityPlanOffer` and the app's metering pair. `SubscriptionGateMiddleware`
is the one place a plan is enforced. Namespace `subscriptions` serves the
plans page, a member's own plan, subscribe and cancel. Its task
`run_billing` materialises the monthly charge. zenobia installs it
unconditionally, because the gate answers "entitled" where the app is
absent, and points `SUBSCRIPTION_PLANS_FILE` at its own ladder.

**`toto.antivirus`** screens file content at the vault's doors and on
demand. Models: `ScanResult`, `ScanPreference`, `ScanRun`, `ScannerConfig`
and a metering pair; scanners for markup, JSON and PDF live in
`antivirus/scanners/`. A finding never changes a file. Every caller goes
through `toto.vault.scanning`, which answers "clean, not scanned" where the
app is absent. On-demand scans run through `toto.workflows`. zenobia does
not install it since 2026-10-03.

**`toto.comments`** has one model, `Comment`, which knows who wrote it and
what it answers but not what it is attached to: an app that wants comments
declares its own through-table with a real foreign key. It has no URLs of
its own. zenobia does not install it since 2026-10-04; its only user there
was a host app that left with the map.

**`toto.editor`** is the shared in-browser file editor (ACE) with no models.
It registers nine vault editor plugins, for the file types `text`,
`markdown`, `json`, `yaml`, `xml`, `csv`, `html`, `latex` and `bib`, serves
them under namespace `editor`, and has a WebSocket consumer and routing
module for live sync. It shows extra buttons where `toto.repo`,
`toto.texlab` or `toto.aralia` are installed. zenobia does not install it
since 2026-10-03.

**`toto.jess`** is a mail transport configured in the database.
`EmailProvider` is the transport, `MailMessage` the outbox and
`InboundMessage` the inbox; `toto.jess.backend.JessEmailBackend` queues
every send to the task `send_mail_message`, and staff pages live under
namespace `jess`. Its password is kept in a gervazy strongbox of its own.
zenobia retired it on 2026-09-24 and sends through Django's own SMTP
settings instead.

**`toto.mail`** is mailboxes: a person's own external mail account
(`Mailbox`, `Keyholder`, `MailAttachment`, `SentRecord`), sealed under
their strongbox passphrase, and one governed system mailbox. It has no
URL module in this tree and sends through `toto.jess`, which
`toto.features` therefore switches on whenever `BUILD_MAIL` is set. zenobia
does not install it.

Two members of this package are not apps. **`toto.ui`** exports
`PageProcessor`, the shared page context (brand, theme, navigation, local
fonts). **`toto.ingress`** provides `IngressCommand`, the base class of
every `ingress_<app>` seeding command.

### 5.2 toto-auth

Every way a person signs in. It depends on `toto-base` and on `PyJWT`,
`cryptography` and `requests`. A host chooses one of three modes with
`toto.auth_config` (section 6): provider, consumer or local.

| App | Tables | zenobia |
|---|---|---|
| `toto.sso_core` | yes | yes |
| `toto.sso_master` | yes | yes |
| `toto.social_login` | yes | yes |
| `toto.sso_client` | yes | no |

**`toto.sso_core`** holds what both federation modes share: the pairing
wire format (`enrollment.py`), QR helpers, the SSO strongbox (`vault.py`),
the password-reset and account-recovery logic, and the model
`RecoveryTicket`. It has no URL module; it adds recovery cards to the
profile through a profile plugin. It uses `toto.audit`, `toto.jess`,
`toto.people` and `toto.socialhub` when present. zenobia installs it.

**`toto.sso_master`** makes the host an OpenID Connect provider. Models:
`SSOClient`, `SSORelyingParty`, `SSOSubject`, `SSOAuthorizationCode`,
`SSOSigningKey`, `SSOAccessToken` and `SSOFederationInvite`. Under
namespace `sso` it serves sign-in and sign-out, the discovery document,
JWKS, authorize, consent, token and userinfo, the registration API, the
federation pairing doors and the profile redirect. Sign-out is a POST with
a CSRF token. The commands `create_sso_relying_party` and
`create_sso_signing_key` provision it. zenobia installs it and runs in
provider mode only.

**`toto.social_login`** adds "Continue with Google" and "Continue with
Facebook" as hand-written OAuth 2.0 flows. Its one model, `SocialIdentity`,
links a provider's subject to a local account. It is inert until a
provider's client id and secret are configured, which is why every mode
installs it. zenobia installs it.

**`toto.sso_client`** makes the host a consumer of another toto platform's
provider. Models: `OIDCProviderConfig` and `FederatedIdentity`; it serves
login, callback and logout under the same namespace `sso`, so
`LOGIN_URL = "sso:login"` holds in every mode. zenobia does not install it:
its settings refuse any `TOTO_AUTH_MODE` other than `provider`.

### 5.3 toto-flow

The workflow engine and the notebook kernels. It depends on `toto-base`.

| App | Tables | zenobia |
|---|---|---|
| `toto.workflows` | yes | yes |
| `toto.mandragora` | yes | no |

**`toto.workflows`** runs directed acyclic graphs of nodes on Celery.
Models: `Workflow`, `WorkflowNode`, `WorkflowEdge`, `LambdaFunction`,
`WorkflowRun`, `WorkflowNodeRun`, `WorkflowEdgeRun`, `ReportTemplate`,
`Report`, `ReportPage` and a metering pair. Node types are lambda, split,
join, report and predefined task. Namespace `workflows` serves the pages
and a JSON API. Its `ready()` imports `<app>.predefined_tasks` from every
installed app; section 6 describes that registry and dispatch-only nodes.
`toto.features` resolves `workflows` to true on every host, with no flag
that can turn it off. zenobia installs it.

**`toto.mandragora`** is Jupyter-style notebooks on managed kernels.
Models: `ComputeKernel`, `KernelDependency`, `Notebook`, `Cell`. Code runs
in a separate kernel server process (`run_kernel_server`), not in Django.
A notebook can also be a `.tpy` file in the vault, opened through a vault
editor plugin. Since 2026-10-01 `toto.workflows` no longer depends on it.
`FEATURE_APPS["workflows"]` still lists both apps; zenobia adds
`toto.workflows` by name and does not install the notebooks.

### 5.4 toto-economy

A double-entry ledger and the metered billing on it. It depends on
`toto-base`. Apps in other packages never import it: they call
`toto.quota.charge`, which does nothing where there is no rate card.

| App | Tables | zenobia |
|---|---|---|
| `toto.assets` | yes | yes |
| `toto.tariffs` | yes | yes |
| `toto.mint` | yes | yes |
| `toto.tax` | yes | yes |
| `toto.mana` | yes | yes |
| `toto.clearing` | yes | no |

**`toto.assets`** is the ledger: `Asset`, `LedgerAccount`, `AssetHolding`,
`LedgerTransaction`, `LedgerEntry` and its hash chain (`LedgerHash`),
currency issuers and contracts, wallet authorisation and the wallet PIN,
platform keys, and faucets (`Faucet`, `FaucetMember`, `FaucetPayout`,
`FaucetRun`). Namespace `assets` serves the wallet and the ledger pages; the
task `run_faucet_hour` pays faucets; a profile plugin adds the Wallet tab.
It must be installed before `toto.tariffs`. zenobia installs it.

**`toto.tariffs`** is the rate card: `Tariff`, `TariffItem`, `UsageRecord`,
`UsageCharge`. `tariffs/charge.py` finds a member's tariff, checks they can
afford an action and charges their prepaid account; `toto.quota.charge`
is the façade other apps call. zenobia installs it.

**`toto.mint`** is the issuance desk: it engraves a currency, mints into
its reserve and burns from it, and keeps `IssuanceRecord`,
`CurrencyMintEvent` and `LedgerAudit` as the record of who created money
and why. Namespace `mint`. It is meant for the one host that holds an
issuer key. zenobia installs it.

**`toto.tax`** is recurring levies on capacity, such as storage per
gigabyte-day. `TaxRule` says which metric is levied and whether it is
armed, `TimeGrant` records a paid raise of a time limit, and
`TaxArrearsCase` tracks arrears. The task `run_daily_levy` runs the sweep,
and apps declare what can be levied in `<app>/taxes.py`. zenobia installs
it.

**`toto.mana`** shows a member three pools — security, compute and storage
— that refill hourly. A pool's balance is an ordinary ledger holding; the
app owns only `ManaPool` (which asset a role is and how fast it refills)
and `ManaGrant`. It registers a chip in the app bar through a header plugin
and a profile section, and its task `regenerate_hour` refills the pools at
the speed the member's clearances set. zenobia installs it.

**`toto.clearing`** federates two platforms' ledgers with signed messages:
`LedgerPeer`, `SharedAsset`, `ClearingHold`, `ClearingOutbox`,
`ClearingInbox`, `ClearingTransfer`, and three tasks for delivery, redrive
and hold expiry. zenobia does not install it since 2026-10-01; it needs a
paired child platform.

### 5.5 toto-ops

Operations. It depends on `toto-base` and has one app.

**`toto.monit`** is a read-only monitoring dashboard with alerts. Models:
`Snapshot` (periodic samples), `CheckState` (the scheduled checks' last
verdicts), `TaskRun` and `BeatEntry` (a heartbeat for every scheduled
task). Namespace `monit` serves the overview, `status/`, `jobs/`,
`history/` and a public `health/` endpoint. Its tasks are `monit_sample`,
`monit_prune` and `monit_alert_checks`; the last one mails the operators in
`ALERT_EMAILS` when a check changes state. It guards on `toto.assets`,
`toto.audit`, `toto.quota`, `toto.subscriptions` and the two Tor apps that
are not in this tree. zenobia installs it when `BUILD_MONIT=1`.

### 5.6 toto-chat

The forum. It depends on `toto-base` and has one app.

**`toto.forum`** is group chat in rooms with permanent, searchable
history. Models include `ForumChannel`, `ForumMember`, `ForumMessage`, room
polls (`RoomPoll`, `PollChoice`, `PollBallot`), `ForumRetentionPolicy`,
`ForumCleanupRun` and `ForumRoomKey`. It has HTML pages and a JSON API
under namespace `forum` and a Django Channels consumer in
`forum/routing.py`; it is the reason a host needs a channel layer. Its
tasks `forum_cleanup` and `forum_expire` enforce retention and expiry, and
its cleanup is a dispatch-only workflow node. zenobia parked it on
2026-10-04 together with its whole WebSocket layer, and unpinned the
package.

### 5.7 toto-geo

The map and the weather on it. It depends on `toto-base` and `toto-flow`.

| App | Tables | zenobia |
|---|---|---|
| `toto.locations` | yes | no |
| `toto.weather` | yes | no |

**`toto.locations`** moved here from `toto-base` on 2026-10-04 with every
geometry-bearing piece. Models: `Address`, `Territory`, `Zone`, `Route`,
`RouteChain`, `MapLayer`, `MapLayerPolygon`, map domains (`MapDomain`,
`MapDomainClearance` and one through-table per kind of item), and the links
from the rest of the platform to the map: `Home` and `HomeSharing` for a
person, `EventPlace` for an event, `CommunitySeat` for a community.
Namespace `locations`. It runs with or without GIS: a host sets `HAS_GIS`,
and with it off the app loads without GeoDjango and uses the second
migration graph `migrations_nogis/`. A host that wants the map pins
`toto-geo` and adds `toto.registry.LOCATIONS_APPS` after `CORE_APPS`. It
keeps map domains to clearances and registers a clearance target plugin.
zenobia installs neither app and has no GIS.

**`toto.weather`** stores observations and forecasts for map addresses
(`WeatherSettings`, `WeatherObservation`, `ForecastSession`,
`ForecastPoint`), fetched through workflow runs and a beat task,
`auto_refresh_weather`. `toto.features` refuses `BUILD_WEATHER` without
`BUILD_GEO`. zenobia does not install it.

### 5.8 toto-works

Content apps over the vault. It depends on `toto-base` and `toto-geo`.

| App | Tables | zenobia |
|---|---|---|
| `toto.kanban` | yes | no |
| `toto.cyprian` | metering only | no |
| `toto.memo` | metering only | no |
| `toto.sketch` | metering only | no |

**`toto.kanban`** is project boards: `Project`, `Campaign`, `Mission`,
`Sprint`, `Task`, practitioners and commitments, submissions and reviews
with consensus policies, reward policies and grants, mission attachments
and documentation pages. It is the app with foreign keys to map models,
which is why the package depends on `toto-geo`. It uses `toto.assets` and
`toto.cyprian` when present.

**`toto.cyprian`** is a document writer over ordinary `html` vault files,
with a bridge that lets another app's page be written in it. It registers a
vault access plugin. It uses `toto.antivirus`, `toto.notarius` and
`toto.verbena` when present.

**`toto.memo`** is slide decks: a deck is one self-contained file in the
vault, edited and presented in the browser. It registers vault editor and
play plugins. Its editor uses `toto.cyprian`'s editor pieces, imported
inside the view that needs them, so a host can mount the reader alone; that
shared code is why the two are in one package. Both apps import
`toto.editor.views` and `toto.antivirus.sanitize` at module level, which
are modules of `toto-base` and need not be installed as apps for that.

**`toto.sketch`** is an SVG drawing board; a drawing is an ordinary `svg`
vault file. It registers a vault editor plugin and refuses to pass
`manage.py check` unless `toto.antivirus` is installed.

zenobia installs none of the four and unpinned the package on 2026-10-01.

### 5.9 toto-business

The company register. It depends on `toto-base` and `toto-geo`.

| App | Tables | zenobia |
|---|---|---|
| `toto.company` | yes | no |
| `toto.voting` | yes | no |
| `toto.ledger` | yes | no |
| `toto.documents` | none | no |

**`toto.company`** models companies, parties, share classes and holdings
as an event-sourced register, departments, memberships and company actions.
**`toto.voting`** models meetings, frozen rolls, propositions, ballots and
tallies, and does not know what is being voted on. **`toto.ledger`** is a
generic append-only chain of entries with checkpoints and database-level
immutability triggers (its second migration installs them); this is a
different thing from the money ledger in `toto.assets`. **`toto.documents`**
builds export HTML and has no renderer of its own. zenobia installs none of
them; its `requirements.toto.txt` says the register left for a separate
product on 2026-09-20.

### 5.10 toto-ai

Assistants. It depends on `toto-base`.

**`toto.steven`** is the assistant that works on a selection: providers,
agents, personalisation and runs (`AiProvider`, `AiAgent`,
`AiPersonalization`, `AiRun`), queued on a worker through a predefined
workflow task, with a floating widget. **`toto.sabbia`** is an older
headless chat-agent backend over a WebSocket (`Agent`, `Conversation`,
`ChatMessage`). **`toto.vicuna`** is a registry of Ollama servers and
models and an embedding service. zenobia installs none of them.

### 5.11 toto-graph

The Neo4j knowledge graph. It depends on `toto-base`, `toto-flow` and
`toto-ai`.

**`toto.ravioli`** is the only app that opens a connection to Neo4j; it
also keeps saved Cypher queries. **`toto.sql_neo4j_sync`** declares the
graph's shape in YAML and projects SQL rows into it. **`toto.bento`** edits
node categories, edge types and the nodes and edges themselves.
**`toto.ingestor`** turns text into a reviewable graph patch.
**`toto.connectors`** pulls external APIs into the same review flow on a
schedule. **`toto.formica`** is a "colony" of agents that curates the graph
over time. **`toto.neo_editor`** is a vault editor plugin for `.neojson`
files and has no models. `bento`, `ingestor` and `connectors` refuse to
start unless the apps they build on are installed; those guards are listed
as hard edges in `check_package_graph.py`. zenobia installs none of them.

### 5.12 toto-media and toto-media-ops

Two media packages, split by weight. `toto-media` depends on `toto-base`
only; `toto-media-ops` depends on `toto-base` and `toto-flow`.

**`toto.vod`** (in `toto-media`) plays audio and video that is already in
the vault, through a vault play plugin and a library page; it has no models.
**`toto.ocr`** (in `toto-media`) reads text off a scan, one Celery task per
page, with `OcrRun` and `OcrPage` and a nightly cleanup task.

**`toto.manta`** (in `toto-media-ops`) builds and runs ffmpeg and ffprobe
commands over vault files. **`toto.fileservices`** is the run substrate for
background operations on a file (`FileServiceRun`). **`toto.transcription`**
models transcription jobs and transcripts and has no pages.

zenobia installs none of the five. The description of `toto-media-ops` in
its own `pyproject.toml` is "installed nowhere"; this repository cannot
confirm that for hosts other than zenobia.

### 5.13 toto-repo

Version control. It depends on `toto-base` and `toto-flow`.

**`toto.repo`** turns a vault directory into a real git repository on the
host's disk (`GitRepo`, `GitRepoFile`, `GitRun`), with commit, branch,
merge, history and push or pull to a git URL. **`toto.gitea`** keeps
per-user accounts on a co-deployed Gitea and samples its storage
(`GiteaAccount`, `GiteaForgeSample`). Each works without the other.
zenobia installs neither since 2026-10-01.

### 5.14 toto-ambrosia and toto-anastasia

Workspaces and booked compute. Each depends on `toto-base` only.

**`toto.ambrosia`** is the workspace base: a folder with a file tree,
settings and hibernation (`Workspace`, `WorkspaceHibernation`,
`AmbrosiaSettings`). The language labs that sit on it are host apps, not
library apps. **`toto.anastasia`** is booked compute: a member reserves
capacity (`ComputeLease`), mounts a capsule (`CapsuleRuntime`) and runs
jobs in it (`Execution`); the package installs the console script
`anastasia-executord`. zenobia installs neither.

### 5.15 limbo

`limbo/` holds three parked apps, each with a `PARKED.md` saying why:
`datalink` (row replication between hosts), `hesperis` (a bounty board) and
`polls`. They are in no package, ship in no wheel and are imported by
nothing.

---

## 6. Mechanisms a host must know

### 6.1 Feature flags: `toto.features`

`resolve_features(get)` turns `BUILD_*` and `INSTALL_*` flags into a frozen
`Features` object. `get` is any accessor from a name to a raw value or
`None`: `os.environ.get` in host settings, a config dictionary's `.get` in
deploy tooling. Both sides call the same function, so the image a deploy
builds and the apps the settings install cannot disagree. A flag is on only
when its value is the string `"1"`.

What the resolver does beyond reading flags:

- **Tiers are defaults.** `BUILD_REALTIME` (still read under its old name
  `BUILD_STUDIO`) is the default for `BUILD_CHAT` and `BUILD_WEATHER`;
  `BUILD_NEO4J` is the default for `BUILD_GRAPH`; `BUILD_MEDIA` is the
  default for `BUILD_VOD`. An explicit per-feature flag always wins.
- **`workflows` is always true.** `BUILD_WORKFLOWS` is no longer read.
- **Closures.** `BUILD_MAIL` turns `jess` on. `BUILD_CONNECTORS` or
  `BUILD_FORMICA` turns `graph` on. `BUILD_STEVEN` turns `sabbia` on.
- **Refusals.** `BUILD_WEATHER` or `BUILD_TRAVELS` with `BUILD_GEO=0`
  raises `FeatureConfigError`.
- **Derived values.** `needs_channels` is true when `chat`, `sabbia` or
  `canasta` is on. `realtime` says whether the image needs the Celery
  layer. `tesseract`, `ffmpeg`, `texlive` and `weasyprint` say which native
  layers the image needs. `notify` is deliberately not part of
  `needs_channels`.
- **Two flags default to on:** `BUILD_GEO` and `BUILD_KANBAN`.

The same module resolves the seeding mode: `ingress_mode(get)` reads
`INGRESS_MODE` (`none`, `realistic` or `full`) and the older `FULL_INGRESS`
and refuses when the two disagree.

### 6.2 App lists: `toto.registry`

| Name | What it is |
|---|---|
| `CORE_APPS` | The nine `toto-base` apps every host installs, in order: `core`, `api`, `gervazy`, `vault`, `people`, `socialhub`, `events`, `verbena`, `quota`. |
| `AUTH_APPS` | The provider block: `sso_core`, `sso_master`, `social_login`. A host that chooses its mode uses `toto.auth_config.auth_apps(cfg)` instead. |
| `BASE_APPS` | `CORE_APPS` followed by `AUTH_APPS`. |
| `LOCATIONS_APPS` | `["toto.locations"]`. Never part of the lists above; a host with the map adds it after `CORE_APPS` and sets `HAS_GIS`. |
| `NOTIFY_APPS` | `["toto.notify"]`. Never part of the lists above; a host adds it and mounts `toto.notify.urls`. |
| `FEATURE_APPS` | A map from a `Features` key to the apps that feature installs. |
| `TASK_MODULES` | The app labels whose `tasks` module Celery autodiscovers. |
| `has_app(name)` | `apps.is_installed(name)`. |

`toto.audit`, `toto.comments` and the six economy apps are in none of
these lists, and `toto.subscriptions` and `toto.antivirus` appear only in
`FEATURE_APPS`; a host names them itself. zenobia writes its
`INSTALLED_APPS` out by hand rather than composing it from `CORE_APPS`, so
the two are kept in step by review, not by code.

### 6.3 Celery: task modules, the beat schedule, the mail tasks

**Task modules.** A host's Celery app calls
`autodiscover_tasks(toto.registry.TASK_MODULES)`. That list is the only
source: being in `INSTALLED_APPS` does not make an app's tasks
discoverable. An entry for an app that is not installed is harmless,
because Celery ignores a module it cannot import.

**The beat schedule.** `toto.schedules.beat_schedule(...)` builds
`CELERY_BEAT_SCHEDULE` from keyword switches, and imports Celery only when
one of them is on. The entries:

| Switch | Task | Default cadence |
|---|---|---|
| `housekeeping` | `toto.core.tasks.nightly_housekeeping` | 03:05 daily |
| `notify_prune` | `toto.notify.tasks.prune_read` | 03:20 daily |
| `gitea` | `toto.gitea.tasks.gitea_sample_storage` | 03:30 daily |
| `vault_trash` | `toto.vault.tasks.purge_expired_trash` | 03:50 daily |
| `tax` | `toto.tax.tasks.run_daily_levy` | 04:15 daily |
| `forum_cleanup` | `toto.forum.tasks.forum_cleanup`, and `forum_expire` every 5 minutes | 04:40 daily |
| `ocr_cleanup` | `toto.ocr.tasks.ocr_cleanup` | 04:50 daily |
| `subscriptions` | `toto.subscriptions.tasks.run_billing` | 05:05 daily |
| `faucets` | `toto.assets.tasks.run_faucet_hour` | minute 7 of every hour |
| `mana` | `toto.mana.tasks.regenerate_hour` | minute 13 of every hour |
| `sweep` | `toto.quota.tasks.sweep_stuck_runs` | minute 41 of every hour |
| `monit` | `toto.monit.tasks.monit_sample`, and `monit_prune` at minute 17 | every 2 minutes |
| `alerts` | `toto.monit.tasks.monit_alert_checks` | every 5 minutes |
| `clearing` | `dispatch_outbox` every minute, `redrive_failed` every 15, `expire_holds` every 5 | — |
| `weather` | `toto.weather.tasks.auto_refresh_weather` | every 30 minutes |
| `connectors` | `toto.connectors.tasks.connectors_scan_schedules` | every minute |
| `formica` | `toto.formica.tasks.formica_beat_scan` | every 5 minutes |
| `anastasia` | `toto.anastasia.tasks.reconcile` | every 2 minutes |

zenobia switches on `monit`, `alerts`, `tax`, `faucets`, `mana`,
`subscriptions`, `sweep`, `vault_trash`, `housekeeping` and `notify_prune`,
each behind a setting of its own.

A task that is scheduled but whose app label is missing from
`TASK_MODULES` is enqueued forever and discarded by the worker with a
`KeyError`. That happened twice, so `toto.tests_schedules` now switches on
every feature of `beat_schedule` and asserts that each task's label is in
`TASK_MODULES` and that the task lives in `<app>.tasks`.

**The mail task list.** `toto.core.tasks.MAIL_TASKS` names every task in
the library that sends mail: `toto.core.tasks.deliver_notice` and
`toto.jess.tasks.send_mail_message`. A host that wants to keep its SMTP
password away from the general worker routes exactly these names to a
queue of their own and gives the password only to that queue's worker.
zenobia does this with a `mail` queue. A test in `toto.core` reads every
task module in the library and fails when a task outside the list sends
mail.

### 6.4 The login strategy: `toto.auth_config`

`resolve_auth(get)` reads `TOTO_AUTH_MODE` (`provider`, the default;
`consumer`; `local`), `SSO_OPEN_REGISTRATION`, `TOTO_SOCIAL_SIGNUP`,
`LOGIN_RETRY_COOLDOWN_SECONDS`, `CAPTCHA_RETRY_COOLDOWN_SECONDS` and
`TOTO_LOGIN_REDIRECT` into a frozen `AuthConfig`. Four functions feed it
into the settings:

- `auth_apps(cfg)` returns the apps for the mode.
- `auth_urlpatterns(cfg)` returns the URL mounts. Every mode serves the
  namespace `sso`, so `LOGIN_URL = "sso:login"` is right everywhere.
- `login_url(cfg)` returns `"sso:login"`.
- `authentication_backends(cfg)` returns the sign-in lockout backend
  (`toto.core.signin_lockout.SigninLockoutBackend`) followed by Django's
  `ModelBackend`. The lockout authenticates nobody and must stay in front
  of every backend that compares a password.

### 6.5 Plugins

Apps extend each other without importing each other. The mechanism is
`toto.core.plugin_autodiscover.autodiscover_plugins("<module>")`: it walks
`INSTALLED_APPS` and imports `<app>.<module>` from every app that has one.
An app that is not installed is never imported, so its contribution is
simply absent. Classes built on `toto.core.plugin.BasePlugin` register
themselves in their base class's `registry`.

| Registry | Base class | Discovered from | Asked by |
|---|---|---|---|
| Profile sections | `socialhub.plugins.profile_plugins.ProfilePlugin` | `<app>/plugins/profile_plugins.py` | The profile page; each plugin names the tab it sits on. |
| Community sections | `socialhub.plugins.community_plugins.CommunityPlugin` | `<app>/plugins/community_plugins.py` | The community page. The forum section shows only where `toto.forum` is installed. |
| Clearance targets | `socialhub.plugins.clearance_plugins.ClearanceTargetPlugin` | `<app>/plugins/clearance_plugins.py` | The *New clearance* dialog: which kinds of group a clearance can keep. `toto.vault` registers buckets, `toto.locations` map domains. |
| Vault play | `vault.plugins.VaultPlayPlugin` | `<app>/plugins/vault_play_plugins.py` | The vault's Play button, by file type. |
| Vault editors | `vault.plugins.VaultEditorPlugin` | `<app>/plugins/vault_editor_plugins.py` | The vault's Edit button and "New file" menu, by file type. |
| Vault access | `vault.plugins.VaultAccessPlugin` | `<app>/plugins/vault_access_plugins.py` | Who may write a file another app lends out. |
| File services | `vault.plugins.FileServicePlugin` | `<app>/plugins/file_service_plugins.py` | Background operations offered on a file. |
| Storage adapters | `vault/storage_adapters.py` | `<app>/plugins/storage_adapters.py` | The kinds of bucket Storage → Management can make. |
| Header widgets | `core.plugin.HeaderPlugin` | registered by explicit import in the app's `ready()` | `{% render_header_plugins %}` in the app bar. `toto.notify` registers the bell, `toto.mana` the mana chip. |
| Floating widgets | `core.plugin.FloatingPlugin` | registered by explicit import | `{% render_floating_plugins %}`. `toto.quota` and `toto.steven` each register one. |
| Personal data | `core.personal_data.PersonalDataPlugin` | `<app>/plugins/personal_data_plugins.py`, on the first export | The data export, for tables of apps the library cannot import. `toto.notify` registers one. |
| Predefined workflow tasks | `workflows.predefined_tasks.register` | `<app>/predefined_tasks.py` | The workflow engine's predefined-task node. |
| Metrics, time limits, sweeps, fee sources | `toto.quota` registries | `<app>/metrics.py`, `times.py`, `sweeps.py`, `fees.py` | Metering, see 6.8. |
| Levy providers | `toto.tax` | `<app>/taxes.py` | The daily levy. |
| Entitlements | `toto.subscriptions` | `<app>/entitlements.py` | The plan catalogue. |
| Scanners | `toto.antivirus` | `<app>/scanners` | Content screening. |

Other packages add registries of their own in the same way
(`toto.locations` has five, `toto.kanban`, `toto.cyprian`, `toto.repo` and
`toto.steven` one each).

When `VAULT_STORAGE_ONLY` is true, the vault draws no Play and no Edit
button whatever plugins are registered.

### 6.6 Clearances

A `socialhub.Clearance` is named after what it opens, for example
`internal` or `confidential`. A person holds clearances through
`Person.clearances`. A platform has at most seven (`MAX_CLEARANCES`). Only a
superuser makes one or puts somebody in.

The rule is in `toto/socialhub/clearance_access.py`:

- **Clearances go on groups, never on single items.** Today the groups are
  vault buckets (`vault.BucketClearance`) and, on a host with the map, map
  domains (`locations.MapDomainClearance`).
- An item in **no kept group** follows its app's own rule.
- An item in **kept groups** is read by superusers and by whoever holds, for
  *every* kept group it is in, at least one of that group's clearances. The
  rule is pessimistic. Nobody else reads it: not its owner, not its
  creator, not through a public flag, not through a folder's access list.
- A user with no `Person` holds none, and neither does an anonymous
  visitor.
- **Hidden is missing.** An item a reader may not read answers as one that
  does not exist: doors answer 404, and lists, counts and exports leave it
  out.

`group_gate(user, queryset, groups=…, open=…)` is the queryset form and
`group_hidden(user, groups)` the per-object form; both use the same
subqueries. `set_clearances` and `clearances_of` change and read a group's
clearances and record the change on the audit chain.

A clearance carries two things only: reading, and the speed at which the
holder's mana pools refill (`regen_security`, `regen_compute`,
`regen_storage`). It grants no rights. Rights come from
`CommunityPrivilege`, a row a *community* carries; a person holds the union
across their communities, and `toto.socialhub.privileges` is the one
resolver. `socialhub/PRIVILEGES.md` maps each right to the gate that reads
it.

### 6.7 The vault's access functions

Any code that hands out or changes a vault file asks `toto/vault/access.py`
rather than writing a rule of its own:

| Function | Answers |
|---|---|
| `may_read(user, vault_file)` | May this user read these bytes? The bucket's clearances are asked first and, where the bucket is kept, decide alone. Otherwise: superuser, the file's owner, a public file, the bucket's owner, or a directory access list. An anonymous visitor gets the public case only. |
| `may_write(user, vault_file)` | May this user change these bytes: hold the editing lock, cut a version, restore one? A superuser who is also on the Superuser plan may; otherwise nobody writes a file its bucket hides from them, the owner writes their own file, and a lending app's plugin may widen that. Reading is wider on purpose: a public flag, a folder's access list and owning the bucket all read and none of them writes. |
| `may_edit_via_app(user, vault_file)` | Whether the `VaultAccessPlugin` registered for this file type lets this user write a file its app lent out. It only ever widens, and a plugin that raises counts as "no". |
| `gate_by_bucket(user, queryset, open=…)` | The queryset form of the bucket clearance rule. |
| `bucket_hidden(user, vault_file)` | The per-object form of the same rule. |
| `is_local_content(vault_file)`, `local_content_q()` | Whether the bytes live on this host. Rewriting content is for local files only. |
| `encrypted_lock_response`, `remote_lock_response`, `mirror_lock_response` | The refusal pages for an encrypted file, a remote file and a mirrored row. |

`toto.vault.filetree.accessible_files(user, …)` is `may_read` as a queryset
for listings. Deleting goes through `toto.vault.trash` (`remove_file`,
`restore_file`, `purge_expired`), versions through `toto.vault.versions`,
editing locks through `toto.vault.locks`, and content screening through
`toto.vault.scanning`, which answers "clean, not scanned" where
`toto.antivirus` is not installed.

### 6.8 Metering: quota, prices, mana, levies, plans

Metering is five layers, and only the first is in every host.

1. **Limits (`toto.quota`).** An app declares its metrics once, as pure
   data, in `<app>/metrics.py`; the metric code is the one string that names
   the limit, the charge and the idempotency key. The app declares a
   concrete policy and event model from the abstract pair, and calls
   `toto.quota.api.check_quota` before the work and `record_usage` after.
   No policy row means the metric is not limited. Periods follow the
   calendar.
2. **Prices (`toto.tariffs`).** `toto.quota.charge` is the only place a
   library app may reach for money: `price_for`, `check_funds`, `charge`,
   `check_and_charge`, `refund`. `price_for` returns `None` when
   `toto.tariffs` is not installed or the metric has no price, and every
   other function does nothing on `None`. An unpriced metric and a host
   without an economy are the same thing to a caller: free. No call site
   should test whether billing is enabled.
3. **Mana (`toto.mana`).** What a member sees as three pools is ordinary
   ledger holdings in three assets. The pools refill hourly, at the fastest
   speed any of the member's clearances sets.
4. **Levies and time dials (`toto.tax`).** A daily sweep charges for
   capacity held above a free allowance; `toto.quota.levies` reads and arms
   a levy from the limits side. `toto.quota.times` holds time limits as
   dials whose free defaults a member can pay to raise; without
   `toto.tax` every dial stays at its default.
5. **Plans (`toto.subscriptions`).** `SubscriptionGateMiddleware` enforces
   a plan in one place: an unauthenticated request proceeds, a free or
   unknown entitlement proceeds, a plan that grants the entitlement
   proceeds, a safe method proceeds, and any other request is refused with
   402. A lapsed subscriber can therefore always read and download. A view
   opts out with `@plan_exempt`. The plans are a YAML file
   (`SUBSCRIPTION_PLANS_FILE`; the library's default is
   `subscriptions/plans.yaml`, and a host's file replaces it rather than
   merging). `subscriptions/PLANS.md` documents the schema.

Run records whose worker was killed are closed by one sweeper,
`toto.quota.sweeps`, from policies each app declares in `<app>/sweeps.py`.

### 6.9 Workflows and predefined tasks

A workflow node of type "predefined task" calls a function an app
registered by name:

```python
from toto.workflows.predefined_tasks import register

@register("vault_zip_files", dispatch_only=True)
def zip_files(input_data: dict) -> dict:
    ...
    return {"data": {...}}
```

`WorkflowsConfig.ready()` imports `<app>.predefined_tasks` from every
installed app, in every process, so a task is known to the web server, the
worker and management commands alike. `register_celery` maps a task name to
a Celery task so the node is dispatched asynchronously.

**Dispatch-only nodes.** `dispatch_only=True` marks a node that only its
own app's dispatcher may start. Such a node trusts its input to name a
record the app has already checked and claimed, so nobody may start it by
hand with input of their own choosing: the run-creation door refuses any
workflow that contains one, for staff too. Listing and viewing those
workflows and their runs is unchanged. The dispatch-only tasks today are
`antivirus_scan`, `vault_zip_files`, `vault_refresh_remote_bucket`,
`vault_transfer_files` and `forum_cleanup`.

An app that runs its heavy work this way follows one shape: a `dispatch`
module that checks who may ask and creates the run, a `runner`, a
`predefined_tasks` module, and a `sweeps` module for runs that died.

### 6.10 Notices and mail

`toto.core.notices.send_notice(user, kind, context, to=…)` sends the short
mails about an account and never raises. The kinds are `password_changed`,
`new_sign_in`, `email_change_confirm`, `email_changed`, and two for the
operators, `check_alert` and `check_recovered`.

Where the host sets `NOTICES_VIA_WORKER`, a notice is rendered in the
caller, in the member's language, and handed to the task `deliver_notice`
after the transaction commits. The task tries five times, with waits of 2,
10, 20 and 30 minutes. No wait is longer than half an hour, because a
message left unacknowledged past the broker's visibility timeout would be
delivered to a worker again and the mail would go twice. Without a worker
the notice is sent at once, once. Each outcome lands on
`core.NoticeDelivery`, which keeps a keyed hash of the recipient and the
error's class, never the address, the text or a password.

Three related pieces are also in `toto.core`:

- `checks.py`: a host that sets `REQUIRE_SMTP` gets system checks
  `core.E001` to `core.E006` that stop any management command while the
  mail settings could not send.
- `error_reports.py`: `PlatformExceptionReporterFilter`,
  `PlatformExceptionReporter`, `PlatformAdminEmailHandler` and
  `CrashMailFilter`, for a host that mails crash reports to its operators
  without leaking secrets, cookies, posted values or credential-bearing
  paths.
- `toto.jess`, where installed, is a database-configured transport and
  outbox instead of environment settings.

### 6.11 Notifications: `toto.notify`

`toto.notify.send(recipient, kind, actor=…, collapse=…, link=…, **params)`
writes one `Notification` row and does nothing else: nobody is woken and
nothing is published. A row keeps its kind and parameters, not a sentence;
the sentence is made in the reader's language when the bell is drawn
(`notify/kinds.py`). Sends of one kind that share a `collapse` key and
arrive within ten minutes of an unread row fold into that row with a count.
`send` never raises, and writes nothing for an unknown kind, an inactive
recipient or a recipient who is the actor.

There is one source, the vault's `file_changed` signal
(`notify/sources.py`), and four kinds: `vault.uploaded`, `vault.replaced`,
`vault.trashed` and `vault.restored`. A notification goes to the **owner of
the bucket** and nobody else, only if `vault.access.may_read` lets that
owner read the file where it is, and never to the person who did it.

The bell has three doors, mounted by the host at `notify/`: `api/` (the
unread count and the latest twenty), `api/read/` and `api/read-all/`. Each
answers at once. The page asks `api/` when it has loaded, when its tab is
looked at again, when the panel is opened and after something is marked
read; it never asks on a timer. **No request is held open and there is no
socket.** The long-poll door `api/wait/`, the Redis publish that woke it,
the setting `LIVE_REDIS_URL` and the module `toto.core.live` were removed
on 2026-10-06, and tests assert they stay gone. The app therefore needs no
Channels, no Redis and no setting of its own, and behaves the same under
WSGI and ASGI.

Read notifications are deleted thirty days after they were read
(`prune_read`); unread ones wait. Every caller in the library checks
`apps.is_installed("toto.notify")` first.

### 6.12 Audit

`toto.audit.record`, `change` and `event` append to a hash chain; each
record hashes its own material with its predecessor's digest, so a removed
or edited row is detectable. Values under secret-looking keys are redacted
before they are written. Apps guard their calls with
`apps.is_installed("toto.audit")`, so the trail is optional for a host, and
a host that wants it installs the app before `toto.vault` and adds
`toto.audit.middleware.AuditContextMiddleware` and `FileAuditMiddleware`.
`AUDIT_CHAIN_KEY` names the chain. `manage.py verify_audit` walks it.

### 6.13 Personal data: export, erasure, housekeeping

**Export.** `toto/core/personal_data.py` builds one zip of a member's data:
each table as CSV and JSON, their own vault files, and a README. It never
includes a password hash, a key, a token or a sealed body; a regular
expression over field names (`SECRET_FIELD`) drops them. Two doors build
it with the same function: the console command `manage.py export_user`
and the member's own *Download my data*, which queues
`toto.socialhub.tasks.build_data_export` and puts the zip in their
personal bucket. Apps the library cannot import add tables through a
`PersonalDataPlugin`.

**Erasure.** A member files an `ErasureRequest` from their account. The
only eraser is the console command `manage.py erase_user`: its `plan`
output, from Django's deletion collector, is the report of what will go,
and it closes the member's request in the same transaction.
`toto/core/erasure.py` removes what a row cascade leaves behind: the avatar
file, the bodies of saved file versions, home pins and unused addresses on
a host with the map, membership applications keyed by address, the
member's name and pictures on forum messages, and their username in the
names of their personal bucket and prepaid ledger account.

**Housekeeping.** `toto.core.tasks.nightly_housekeeping` runs Django's
`clearsessions`, deletes sign-in rows whose session is gone and, where
socialhub is installed, prunes membership applications that lapsed more
than `SOCIALHUB_EXPIRED_APPLICATION_DAYS` days ago together with the
never-used accounts they made. Each run is one audit record with counts
only.

**The privacy notice.** `socialhub.PrivacyNotice` is versioned and public;
an applicant accepts the current one, recorded as a `PrivacyAcceptance`. A
host supplies its own text through `PRIVACY_NOTICE_TEXTS`.

### 6.14 Seeding

Every app that seeds data has a command `ingress_<app>` built on
`toto.ingress.IngressCommand`, and `manage.py ingress_all` runs those a
host allows in `INGRESS_ALLOWED_APPS`. There are three modes: `none` seeds
nothing, `realistic` (the default) seeds the rows a working platform
needs, and `full` adds demonstration data. `manage.py init_platform`
creates the administrator and the `Platform` row and refuses an empty
administrator password.

### 6.15 Middleware and context a host adds

| Class | What it does |
|---|---|
| `toto.core.middleware.PlatformMiddleware` | Redirects to the maintenance page while `Platform.active` is false. |
| `toto.core.middleware.ContentSecurityPolicyMiddleware` | Sends the `CONTENT_SECURITY_POLICY` header where the host sets one. |
| `toto.core.middleware.ProfileLanguageMiddleware`, `ProfileTimezoneMiddleware` | The member's language and time zone. |
| `toto.core.middleware.UserSessionMiddleware` | Keeps "last seen" and the address current on a member's list of sessions. |
| `toto.audit.middleware.AuditContextMiddleware`, `FileAuditMiddleware` | The audit context and vault file records. |
| `toto.subscriptions.gate.SubscriptionGateMiddleware` | The plan gate. |
| `toto.api.middleware.TokenAuthMiddleware` | Bearer tokens on WebSockets, for a host with Channels. |

Context processors: `toto.core.context_processors.last_visited` and
`build_flags`, `toto.assets.context_processors.economy_apps`, and
`toto.subscriptions.gate.subscription_context`.

### 6.16 Settings a host sets

The settings below are the ones the apps read by name. The list is not
complete; it is what a new host most needs.

| Area | Settings |
|---|---|
| Filesystem | `TOTO_DATA_DIR` (seed and branding data), `TOTO_RUN_DIR` (runtime bundles). `toto.conf` falls back to paths relative to `BASE_DIR` when they are unset. |
| Version check | The environment variable `TOTO_SKIP_VERSION_CHECK=1` skips the runtime check for local work. |
| Sign-in | `LOGIN_DELAY_AFTER`, `LOGIN_DELAY_MAX_SECONDS`, `LOGIN_LOCK_AFTER`, `LOGIN_LOCK_MINUTES`, `LOGIN_ADDRESS_LOCK_AFTER`, `LOGIN_FAILURE_WINDOW_MINUTES`; `TRUSTED_PROXIES` and `TRUST_X_REAL_IP` for the client address. |
| `toto.core` | `REQUIRE_SMTP`, `NOTICES_VIA_WORKER`, `NIGHTLY_HOUSEKEEPING`, `CONTENT_SECURITY_POLICY`, `DASHBOARD_ITEMS`, `DASHBOARD_CATEGORIES`, `INGRESS_MODE`, `INGRESS_ALLOWED_APPS`, `TOTO_ADMIN_READONLY`. |
| `toto.ui` | `HEADER_NAV_ITEMS`, `USE_EXTERNAL_FONTS`, `BRAND_FROM_FEDERATION`. |
| `toto.vault` | `VAULT_STORAGE_ONLY`, `VAULT_FILE_EDITS`, `VAULT_REFUSED_FILE_TYPES`, `VAULT_ROOT`, `VAULT_TRASH_DAYS`, `VAULT_TRASH_PURGE`, `VAULT_EXTERNAL_BUCKETS`, `VAULT_OUTBOUND_ALLOWED_HOSTS`, `VAULT_OUTBOUND_ALLOW_PRIVATE`, `VAULT_ENCRYPT_ASYNC`, `VAULT_RUN_KEY`, `FIELD_ENCRYPTION_KEY`. |
| `toto.socialhub` | `PRIVACY_NOTICE_TEXTS`, `SOCIALHUB_EXPIRED_APPLICATION_DAYS`, `SOCIALHUB_AVATAR_MAX_BYTES`, `SOCIALHUB_ERASURE_COMMAND`. |
| `toto.subscriptions` | `SUBSCRIPTION_PLANS_FILE`, `SUBSCRIPTION_GATE_READS`, `SUBSCRIPTION_GRACE_DAYS`, `SUBSCRIPTION_ENFORCEMENT`. |
| `toto.quota` | `ECONOMY_STAFF_ONLY`. |
| `toto.audit` | `AUDIT_CHAIN_KEY`. |
| `toto-auth` | `TOTO_AUTH_MODE`, `SSO_OPEN_REGISTRATION`, `SSO_VAULT_PASSWORD`, `PLATFORM_DOMAIN`, `SSO_CLIENT_REQUIRED_ROLES`, `SSO_RELYING_PARTIES`, `TOTO_SOCIAL_SIGNUP`, `RESET_MAILS_PER_ADDRESS_PER_HOUR`, and the Google and Facebook OAuth client ids and secrets. |
| `toto.workflows` | `WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS`, `WORKFLOW_KERNEL_CLIENT`, `WORKFLOW_FILE_CONNECTOR_ROOT`, `WORKFLOW_FILE_CONNECTOR_MAX_BYTES`. |
| `toto.monit` | `ALERT_EMAILS`, `ALERT_REMIND_HOURS`, `MONIT_RETENTION_HOURS`, `MONIT_RUN_RETENTION_DAYS`, `MONIT_BACKUP_DIRS`, `MONIT_OFFSITE_DIR`, `MONIT_CERT_DOMAIN`, `MONIT_REDIS_URL`. |
| `toto-economy` | `GAS_ASSET`, `GAS_ASSET_NAME`, `GAS_SUPPLY`, `GAS_STARTING_GRANT`, `TARIFF_PRICES`, `MANA_PRICES`, `MANA_REGEN_MINUTE`, `TAX_ARREARS_GRACE_DAYS`, `MONETARY_ISSUER_KEY`, `WALLET_VAULT_SECRET`, `FIELD_ENCRYPTION_KEY`. |
| `toto.locations` | `HAS_GIS`, and `MIGRATION_MODULES = {"locations": "toto.locations.migrations_nogis"}` when it is off. |

### 6.17 Version coherence

Three layers refuse a mixed suite; all three live in `toto/versioning.py`,
which is standard-library only so that deploy tooling can import it before
Django is configured.

1. **Build time.** A host's deploy calls `read_manifest` on its
   `requirements.toto.txt` (exact `toto-<name>==<major>.<release>` pins, all
   at one version), `verify_checkout` on the library tree before building,
   and `verify_wheels` on the built wheels after. With `strict=True` the
   tree's `VERSION` must equal the pin, and, where the tree is a git
   checkout, it must sit on the tag `v<version>` with nothing uncommitted.
   A tree without a `.git` directory, such as a vendored copy, is checked
   for its version only.
2. **Install time.** Each package pins its siblings exactly, so pip itself
   refuses a mixed set.
3. **Run time.** `toto.core`'s `ready()` calls `check_runtime_coherence()`,
   which raises `ImproperlyConfigured` when the installed `toto-*`
   distributions differ in version or when a pre-split distribution named
   plain `toto` is still installed.

---

## 7. Working on the library

### Versions supported

Every package declares `requires-python = ">=3.10"`. `toto-base` declares
`Django>=4.2,<6`. The test requirements pin `Django==5.2.17`, which is what
zenobia runs. The code is written so that Django 4.2 also works
(`toto/core/django_compat.py` holds the two differences: the
`CheckConstraint` argument name and the `URLField` default scheme), but no
gate in this repository runs on 4.2 today, so a host that stays on 4.2
tests that itself.

### Branches

Work happens on `dev_django5`, which is also the branch pushed to
`origin`. `main` was last moved on 2026-09-26 and is behind it. The
`legacy/*` branches keep the history of the time before this repository
became the library's home again. Commit and push on the branch that is
checked out; do not switch branches to make a change.

### Installing for development

```bash
scripts/install_toto.sh      # all sixteen packages, editable, one pip call
```

They must go in one call because they pin each other exactly. If a
pre-split distribution named `toto` is installed, remove it first
(`pip uninstall -y toto`); it shadows the namespace.

### The library's own tests

`tests/` is the packaging harness. It ships in no wheel and runs with
pytest (`pyproject.toml` sets `testpaths = ["tests"]`).

| File | What it proves |
|---|---|
| `tests/test_versioning.py` | `VERSION`, every package version and every sibling pin agree; `release.py --check` and the package graph check pass; the manifest, wheel and checkout verifiers accept and refuse what they should; the runtime check refuses a mixed suite. |
| `tests/test_features.py` | The flag contract of `toto.features`: defaults, closures, refusals, and which flags buy which layers. |
| `tests/test_packaging.py` | Against built wheels: no `toto/__init__.py`, no file shipped by two packages, every app's migrations, templates, static files and management commands are in the right wheel, every entry of `TASK_MODULES` ships a `tasks` module. |
| `tests/test_django_check.py` | Against installed wheels, with the source tree unable to shadow them: `toto` resolves from site-packages, `manage.py check` passes, and the map boots, migrates and matches its models both with and without GIS. |
| `tests/tier_check.py` | Run inside a virtual environment that holds one dependency tier only: every app of those packages loads without reaching for a toto module from a package that is not installed. |
| `tests/settings_min.py`, `settings_min_nogis.py`, `urls_min.py` | The minimal host settings the checks run under: `BASE_APPS` plus `toto.locations`, once with GeoDjango on spatialite and once with `HAS_GIS = False` on plain sqlite. |

The packaging tests need built wheels and skip without them. The whole
gate, in fresh virtual environments, is one script:

```bash
PYTHON=/usr/bin/python3 scripts/clean_env_check.sh
```

It runs the package graph and version checks, builds every sdist and every
wheel from its sdist, installs the suite offline, runs pytest, runs the
`toto.jess` suite and the password-reset tests against the installed
wheels, and then installs a list of dependency tiers one at a time and runs
`tier_check.py` in each. Use the system interpreter: `settings_min.py`
loads `toto.locations` with GeoDjango, which needs the system GDAL, and a
conda Python usually cannot load it. The tier list in the script names
eleven combinations and does not yet include `toto-economy`,
`toto-business`, `toto-ambrosia`, `toto-anastasia` or `toto-media-ops`.

### The apps' own tests

The apps carry about five hundred test modules beside their code
(`tests.py`, `tests_*.py`, `tests/`). They are Django tests and need a
settings module, so they run in one of two ways:

- **From a host.** zenobia's gate, `zenobia/scripts/clean_env_test.sh` in
  the monorepo, runs named library test modules with
  `manage.py test toto.<app>.<module>` under `zenobia.settings`, against
  the installed wheels. With `SUITE_TESTS=1` it also runs this
  repository's pytest harness from the vendored copy. Only the apps
  zenobia installs can run this way.
- **From an app's own settings.** Eight apps ship a
  `testing/settings.py` so their suites run without a host:
  `toto.sso_master`, `toto.jess`, `toto.ocr`, `toto.clearing`, `toto.tax`,
  `toto.sketch`, `toto.kanban` and `toto.ravioli`. For example:
  `DJANGO_SETTINGS_MODULE=toto.clearing.testing.settings python -m django test toto.clearing.tests`.

`toto.tests_schedules` (section 6.3) is a library test that a host runs.

### The package graph check

```bash
python scripts/check_package_graph.py
```

At this commit it prints `packages: 16   members: 72` and
`OK: partition holds`. When it reports
`UNDECLARED DEPENDENCY toto-x -> toto-y`, there are three honest fixes, in
order of preference: make the import lazy, when the integration really is
optional; add `toto-y` to `toto-x`'s dependencies, when the need is real and
creates no cycle; or move the app, when the boundary was wrong. The check
also runs inside pytest and inside `clean_env_check.sh`.

### Migrations

On 2026-10-01 every app's migration history was replaced by a fresh
`0001_initial` (some apps need two or three initial files because of
circular references). There is no upgrade path across that reset: a
database is built again on the new graph, and a dump from before it cannot
be restored into a 2.0 database. A host with migrations of its own that
name library migrations must regenerate them.

The policy since then is the owner's: migration history is not kept for its
own sake, fresh databases are assumed, and an app is reset to a fresh
`0001` rather than left to accumulate steps. A few hand-written steps are
kept because they do something a generated initial cannot: `api`'s
`0002_data_mesh_group`, `core`'s `0002_bootstrap_marker`, `ledger`'s
`0002_immutability_triggers`, `kanban`'s `0002_seed_consensus_policies`
and `forum`'s `0002_cleanup_run_workflow`. `people` and `socialhub` have
gained a few ordinary steps since the reset, and `locations` has
`0004_homes_places_seats` in both of its graphs.

`toto.locations` has two graphs with the same file names:
`migrations/` with geometry and `migrations_nogis/` without. They must be
kept in step by hand; `tests/test_django_check.py` runs
`makemigrations --check` in both modes to catch drift.

### Versioning and releasing

The version is `MAJOR.RELEASE`. Raise MAJOR when a host must change
something to follow: a settings contract, a host API signature, an app
label, or a migration that cannot be applied over the old one. Raise
RELEASE for everything else. Version 2.0 (2026-10-02, commit `c01fbc6e`)
was a major release for three reasons: the migration reset, the move to
Django 5.2 with sign-out by POST only, and the removal of several long-kept
compatibility pieces.

`scripts/release.py <version>` is the only thing that writes version
numbers. Never edit them by hand.

```bash
python scripts/release.py --check     # verify, write nothing
python scripts/release.py 2.1         # VERSION, every pyproject, every sibling pin
```

The repository has `v*` tags up to `v1.30` and no `v2.0` tag. Two other
tags mark states worth returning to: `last-with-gis` and
`company-parked-2026-09-28`. A host that builds from a git checkout of this
repository with a strict deploy would need the tag `v2.0` on the commit it
builds; zenobia builds from its vendored copy, which has no `.git`, so the
tag is not asked for.

### How zenobia re-vendors

The working rule has two steps, always in this order:

1. **The library first.** Make the change here, on `dev_django5`, commit
   and push.
2. **Then the vendored copy.** Copy this repository over
   `vendor/toto_libs/` in the monorepo so the two are byte-identical, and
   commit there with **the same commit message**, on the branch the
   monorepo has checked out.

The copy is an rsync from the monorepo's root (the monorepo's
`technology.md`, section "The vendored toto library", is the reference):

```bash
LIB=/home/janek/Desktop/dev/toto_libs/toto_libs
rsync -a --delete --exclude .git --exclude __pycache__ --exclude 'build/' \
      --exclude '*.egg-info' "$LIB/" vendor/toto_libs/
```

When `VERSION` moved, every pin in `zenobia/requirements.toto.txt` moves to
it in the same commit. zenobia's deploy refuses a build when the pins, the
vendored `VERSION` and the built wheels disagree, or when the vendored tree
has uncommitted changes.

A change that only one side needs still goes through both steps when it
touches a file under `vendor/toto_libs/`; never edit the vendored copy
alone.

### Commit style

One line, at most eight words, saying what changed in plain words, for
example `Wait door leaves; bell asks on return` or
`Locations move to toto-geo`. No body, no trailers. One commit covers one
scoped change, so a larger piece of work is a run of small commits.

---

## 8. Recent changes: October 2026

About 310 commits landed between 2026-10-01 and 2026-10-06. The list below
names the ones that changed what a host sees, by day, with their short
hashes.

**2026-10-06 — nothing is held open**

- `591d2a6f` The long-poll door `notify/api/wait/` is removed; the bell
  asks its door when the page loads and when its tab is looked at again.
- `0e06c35e` The vault's file list asks again in the same way; folders are
  no longer watched.
- `650d6f32` The notes in `toto.registry` and `toto.features` say so: no
  long poll, no Redis.

**2026-10-04 — notifications; the map leaves toto-base; the forum guarded**

- `a4892d70` `toto.notify` is added: notifications, the bell, and at first
  a live socket and presence.
- `5300a9ac` Presence is removed: no sign-in toasts and no switch for it.
- `5328335c` Notifications are only about buckets the recipient owns.
- `ffa53f3b` A long-poll door replaces the live socket (and left two days
  later, above).
- `0f04d418` The vault gains a row door, thumbnails and an upload progress
  panel.
- `3a0f6011` `toto.locations` moves from `toto-base` to `toto-geo`.
- `425f2a32`, `99c98f8f`, `7777e850`, `4d300c14` A person's address, an
  event's place and a community's seat become text; the profile loses its
  map picker.
- `36630761`, `1372278d` The map keeps the links (`Home`, `EventPlace`,
  `CommunitySeat`) and takes the map partials and the geo admin with it.
- `0c196010` `toto.registry` and the test settings no longer include
  `toto.locations`; `LOCATIONS_APPS` is added.
- `4ef914b5` `toto-works` and `toto-business` declare their dependency on
  `toto-geo`.
- `70439a11`, `32bb36f7` socialhub names the forum only where it is
  installed, and the API tests no longer assume chat or Channels.

**2026-10-03 — storage only**

- `aac6cf6a` The vault gains the storage-only switch
  (`VAULT_STORAGE_ONLY`), *New folder* and in-page upload.
- `1866ffdd`, `40b2472f` The vault page hands its sentences to scripts as
  data, and door tests set the switch themselves.
- `357074c8` The manual and the tests no longer assume editors or the
  antivirus.
- `e8d45e78` Forum attachments show only raster pictures inline.
- `f94de577` `init_platform` refuses an empty administrator password.
- `f75ea1a8` The wallet PIN locks after five wrong tries.
- `0b834c49` Password-reset mails are limited per account and per address.

**2026-10-02 — version 2.0, Django 5.2, hardening**

- `b68f5322` The library runs on Django 5.2 and keeps 4.2 working.
- `bdd85cf0` Sign-out is by POST with CSRF only.
- `c01fbc6e` The suite is released as 2.0.
- `398b12dc` The vault's zip node and the render node become
  dispatch-only.
- `bb724e86` The mail tasks are listed (`MAIL_TASKS`) so a host can give
  them a queue of their own.
- `5c14f207`, `66ddb49d` CORS trusts exact client origins, and the session
  API doors refuse cross-site cookie writes.
- `4880ddf0`, `f3b07c40` Sign-in tries are counted before the password is
  compared, and failures belong to the account tried.
- `c9c9a186` OIDC consent is approved only by its CSRF-checked POST.
- `617067cb`, `7415550d`, `4f9b1152` Profile plugins carry a tab, the
  profile is drawn in tabs, and the app bar has one Profile entry, a sun or
  moon, and ENG/PL.
- `c33133f2`, `4738cbd1` Clearances keep buckets and map domains only; a
  bucket that served wiki topics is an ordinary bucket.
- `a1a8bd43`, `e2a220a6` Clearances in the admin and map domains need the
  Superuser plan.

**2026-10-01 — the migration reset, privacy, trash, alerts**

- `103236a4` to `cb320aba` Every package's migrations are reset to fresh
  initials (sixteen commits, one per package).
- `11acb919` The GIS-off migration graph of `toto.locations` is derived
  from the fresh initials.
- `4e8f625f`, `bcbe45d2` `Person.is_federal_agent`,
  `Community.is_federal_tribe`, the pre-reset compatibility shims and the
  scripts that mended old databases are removed.
- `890bfe7b` `toto.workflows` no longer depends on `toto.mandragora`
  (`LambdaFunction` loses its kernel key), so a host can install the engine
  without the notebooks.
- `033b8426`, `555f26dd`, `b3e3b129`, `bca2faaf`, `c99711ae` Vault deletes
  move files to a trash with a Trash tab, a nightly purge and bulk moves.
- `ce21d03f` The vault refuses Microsoft Office files and says why.
- `de44aa54`, `e23d6534` A versioned privacy notice, accepted by
  applicants.
- `aa102288`, `de1e6e27` One member's data as a zip, and *Download my
  data*.
- `a1a66807`, `aa8c0fb0`, `f2e8d256` Erasure requests, closed by
  `erase_user`, which also removes what the cascade left behind.
- `9c747083` The nightly housekeeping.
- `399c28e2`, `e76d2b8c` Notices go through the worker with retry, with
  waits under the broker's visibility timeout.
- `6e844ba4`, `da1e0369`, `24641188`, `849674d1` Scheduled checks mail a
  change of state, scheduled runs are recorded and overdue ones flagged,
  and stuck run records are closed.
- `09fef8f4`, `edad2bd7` Error reports star secrets, and a system check
  refuses mail settings that cannot send.
- `00d9ad80`, `975718ca`, `12e82030`, `4a180adb` The Tailwind stylesheet is
  built by the host, the theme font and pictures are served from the
  platform itself, and pages drop HTMX.
- `a236b419`, `2aab7462`, `c716b429` django-reversion, the JSON widget and
  MarkdownX are uninstalled; the workflows admin edits JSON in a plain box.
- `958ecf78` The WebSocket token rides the subprotocol header.

---

## 9. What this file does not vouch for

- **Hosts other than zenobia.** Nothing here was checked against faros,
  aurelian, placidia, poseidon, emilia or delta. Section 2 says only what
  this repository shows.
- **Running code.** This file was written by reading the source. Apart
  from `scripts/check_package_graph.py` and `scripts/release.py --check`,
  which both pass at commit `650d6f32`, no test or gate was run for it.
- **The apps zenobia does not install.** Their descriptions come from their
  models, URL modules, app configs and package READMEs. They are built and
  checked for packaging, but nothing deploys them today, so behaviour
  described for them is what the code says, not what a running platform
  was seen to do.
- **Package READMEs.** They were not revised with this file and several
  are out of date in their counts and in where apps live. The root
  `pyproject.toml` comment, which lists eleven packages, and the
  `*_vendor.sh` scripts are out of date in the same way.
- **Django 4.2.** The declared range includes it; no gate here proves it.
