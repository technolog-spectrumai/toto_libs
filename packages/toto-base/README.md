# toto-base

**toto-base** is the foundation wheel of the toto suite: the one distribution every toto host installs. It carries the *shared host API* (the app lists, feature-flag resolution, ASGI/Celery wiring, filesystem config and version-coherence machinery that a host stitches into its Django settings) together with the irreducible cluster of platform apps that cannot be layered apart — platform identity and branding, people, geography, communities, events, the encryption vault, file storage, usage quotas, backups and the shared content/editor primitives (the OpenID Connect provider/consumer apps ship in the sibling `toto-auth` package). It is one of 10 lockstep-versioned distributions sharing the `toto.*` PEP 420 namespace; hosts pin every toto package to the same version in `requirements.toto.txt`.

---

## What it does (functional)

toto-base is what turns an empty Django project into a running toto platform. An operator who installs it gets:

- **A branded, single-tenant platform.** One `Platform` record defines this deployment's name, domain, contact address and visual identity — a theme built from a colour palette and body/heading fonts, with optional dark mode and custom CSS. The platform can be flipped into maintenance mode, and it belongs to a named `Federation` that groups related deployments.
- **People and communities.** Every human in the system is a `Person` — the shared identity anchor used everywhere, whether or not they have a login. People join `Community` groups through an email-verified application that requires a reference from an existing member. Communities have leaders and senior members, form parent/child hierarchies, publish news posts, adopt a constitution that members can sign, and can be marked as "federal" to unlock special roles elsewhere in the suite.
- **Maps and places.** Addresses pin people, communities and events to points on a map; zones, territories, routes and thematic map layers describe delivery areas, regions and paths. All of it is real GIS data (PostGIS/WGS84), so the platform can answer spatial questions.
- **Events and availability.** Organizers schedule events with a venue, time window and capacity; members are invited, RSVP, and can publish personal availability windows for scheduling.
- **An encryption vault and document signing.** Users protect secrets, files and private keys behind their own password using a layered AES-256 key hierarchy — the server cannot read stored secrets without the user present. The same subsystem gives each person an Ed25519 identity for cryptographically signing contracts and documents, with an append-only audit log of every operation.
- **File storage with in-browser editing.** Users upload files into quota-limited buckets and folders (backed by local disk, S3-compatible object stores, or a remote toto instance) and edit text, JSON, YAML, XML, HTML, CSV, LaTeX and BibTeX files directly in the browser, with live multi-session sync.
- **Single sign-on (via `toto-auth`).** Together with the sibling `toto-auth` package the platform is a full OpenID Connect identity provider — or a consumer of an external toto SSO provider; base carries the shared login implementation both entry points delegate to.
- **Outbound integrations and email.** Operators register connectors to external services (webhooks, LLM providers, SMTP relays) and email-sending profiles — with all credentials kept in the encryption vault, never in plaintext config.
- **Signed, verifiable backups.** Backup archives are cryptographically signed on the way out and verified on the way in, with a checksum-tracked record of each stored archive.
- **Usage quotas and metering.** A generic policy engine records usage events and enforces per-subject limits (track / warn / block) over daily-to-lifetime windows, so any app can meter and cap an activity.

Most of these capabilities are the shared substrate that the other eight toto packages build on top of.

---

## How it works (technical)

toto-base ships two kinds of code under the `toto.*` namespace: **host-API modules** (plain Python that a host imports into its settings/ASGI/Celery config) and **Django apps** (the model layer). `[tool.setuptools.packages.find]` claims exactly the portions present under this package's `src/toto/`; `scripts/check_package_graph.py` enforces that the suite-wide partition stays disjoint.

### The host API (shared modules)

These live directly under `toto/` and have no models. A host composes its Django project from them:

- **`versioning.py`** — version coherence across build time, install time and runtime. Parses a host's `requirements.toto.txt` into a strict `Manifest` (only `toto-<pkg>==<major>.<release>` pins, all at one version — the lockstep rule), verifies a `toto_libs` checkout (`verify_checkout`) and the built wheels (`verify_wheels`), and — via `check_runtime_coherence()` — refuses to boot a mixed-version or half-upgraded install. Stdlib-only at import time because deploy tooling imports it before Django is configured. `toto.core`'s `AppConfig.ready()` calls `check_runtime_coherence()`, making core the single boot-time gate for the whole suite; it also detects the legacy pre-split `toto` distribution shadowing the namespace.
- **`registry.py`** — the canonical `BASE_APPS` list (the toto apps every host installs, in order), the `FEATURE_APPS` map (feature key → apps + third-party companions a feature pulls in), the `TASK_MODULES` list for Celery autodiscovery, and `has_app()` for capability checks. Note that `toto.editor` is a *feature* app here (installed only when an editor feature is on), not a base app.
- **`features.py`** — `resolve_features(get)` collapses coarse build tiers (`BUILD_STUDIO`, `BUILD_NEO4J`) and per-feature `BUILD_*`/`INSTALL_*` flags into a frozen `Features` dataclass of effective build decisions, applying dependency closures (e.g. anything with an FK to `workflows.WorkflowRun` forces `workflows` on; `graph` implies its native binaries). Shared by both host settings and deploy tooling so the flag logic lives in one place.
- **`routing.py`** — `collect_websocket_urlpatterns()` defensively imports each app's `routing.websocket_urlpatterns` (skipping optional apps that aren't installed) for a host's ASGI application.
- **`schedules.py`** — `beat_schedule(...)` builds the `CELERY_BEAT_SCHEDULE` dict for whichever periodic features are enabled; celery is imported lazily so the basic WSGI tier needs no celery.
- **`conf.py`** — `data_dir()` / `run_dir()` resolve host-configurable filesystem locations (`TOTO_DATA_DIR`, `TOTO_RUN_DIR`) with legacy fallbacks.
- **`celery_utils.py`** — `celery_available()` pings for a live worker (1 s timeout).
- **`toto.ui`** — no app; exports `PageProcessor` (from `page.py`), the shared template context/tags providing platform, theme and navigation data to every template.
- **`toto.ingress`** — no models; provides `IngressCommand`, the base class for all `python manage.py ingress_<app>` seed commands (with `DATA_ROOT`, `read_text`/`read_json`, and a `--full` flag).

### The application layer

The apps below install (in `CORE_APPS` order) `core → api → backup → gervazy → vault → people → locations → socialhub → events → verbena → quota`; `registry.BASE_APPS` appends the provider auth block (`sso_core`, `sso_master`), which ships in `toto-auth` since 1.8. As the `pyproject` description notes, `core`, `gervazy`, `people`, `locations` and `events` form one irreducible foreign-key/import cycle — which is precisely why they ship in a single wheel.

Many couplings named below point *out* of this package into sibling wheels (`steven`, `contracts`, `mobilization`, `detections`, `kanban`, `assembly`, `tribunal`, `academy`, `inventory`, `response`, `travels`, `library`, `ocr`, `texlab`, `invoice`, `ravioli`, `palimpsest`, `memo`, `workflows`, …). Those siblings extend base's abstract bases and FK into its models; base itself stays unaware of them (it sees only reverse relations), which is what lets it build and boot standalone.

#### core — platform identity & branding

The first app to boot. `Platform` is the singleton for this deployment (name, domain, contact, theme, active flag, rate-limit settings); `Federation` groups platforms into a network. Branding is `Theme` (a `ColorMix` palette + body/heading `Font`s, dark-mode and custom-CSS overrides). `DomainEntity` is the abstract base almost every domain model inherits — it supplies `uuid`, `slug`, `name`, `description`, `logo`, `metadata`, `created_at`, `updated_at` without extra tables. `core` also owns middleware (`PlatformMiddleware` for maintenance-mode redirects and per-IP rate-limit bookkeeping; `ProfileLanguageMiddleware` for per-user locale; `ContentSecurityPolicyMiddleware` for an opt-in CSP header) and the always-available `graph_export` template tag (renders an "Export to graph" button only when the optional Neo4j layer is present). Its `AppConfig.ready()` runs the suite-wide version check and enables SQLite WAL mode. Standalone (no toto dependencies).

#### people — the identity anchor

`Person` (a `DomainEntity`) is linked one-to-one with an optional `auth.User` — people without login accounts can exist (referenced contacts, external parties). Every domain action FKs into `Person`, never the raw `User`, decoupling platform identity from Django auth. Key fields: `communities` (M2M to `socialhub.Community`), `patron` (self-FK mentor link), `address` (FK to `locations.Address`), `is_federal_agent` (gates responder eligibility in the mobilization sibling), and `digital_signature` (a base64 PNG of a handwritten signature — decorative, distinct from the cryptographic Ed25519 key gervazy manages). Depends on `locations`.

#### locations — GIS substrate

PostGIS models (SRID 4326) extending `DomainEntity`: `Address` (postal fields + `PointField`), `Territory` (`MultiPolygonField`), `Zone` (typed sub-area with a `PolygonField`), `Route` / `RouteChain` (typed `LineStringField` paths), and `MapLayer` / `MapLayerPolygon` (thematic overlays). Enables spatial queries used across the suite (detections in a zone, nearest inventory site, etc.). Ships a small public "Enigma" JSON API under `/locations/api/` for zones and addresses. Depends on `people`.

#### socialhub — communities & membership

`Community` (a `DomainEntity`) is the primary grouping unit: it has a `federation`, an `org_type`, hierarchy (`parent` self-FK), a `head` and `senior_members`, a headquarters `location`/`territory`, and an `email_service` FK. Flags like `is_autonomous`, `is_foreign` and `is_federal_tribe` (elevates a community so members become emergency-responder-eligible) drive behaviour elsewhere. Membership flows through `MembershipApplication` (6-digit email verification) plus a `ReferenceRequest` from an existing member. Communities publish `CommunityNewsPost`/`CommunityNewsTopic` (built on verbena's section/tag bases) and adopt a `Constitution` that members endorse via `ConstitutionSignature`. Public `/socialhub/api/` profile and community endpoints. Depends on `api`, `locations`, `people`.

#### events — scheduling & availability

`EventBase` (abstract, extends `DomainEntity`) carries `category`, `title`, time window, `is_public`/`is_cancelled` — it is the parent of both `ScheduledEvent` (concrete: owner/organizers as `people.Person`, venue `locations.Address`, capacity, `socialhub.Community`) and, in a sibling, `detections.Detection` (time-anchored events reusing the same fields). `EventInvite` tracks RSVPs; `Availability` records a person's free/blocking windows (with JSON recurrence). Depends on `locations`, `people`.

#### gervazy — encryption-at-rest & signing

Implements a three-tier AES-256-GCM key hierarchy: a user password runs through Argon2id to derive a UKEK (never stored) → unwraps a per-user `VaultMasterKey` → wraps namespace-scoped `WrappedDataKey`s → those DEKs encrypt `EncryptedSecret`, `EncryptedFile` (+ sequential `EncryptedFileChunk`) and `EncryptedPrivateKey` blobs. Decryption requires the user's password at runtime. A separate Ed25519 signing layer (`signing.py`) stores each person's private key encrypted in their `UserStrongbox` and exposes a stateless `SigningService` (`provision_signing_key`, `sign_document`, `verify`, `canonical_contract_payload`) that opens a short-lived `GervazyCryptoSession`, produces a `DocumentSignature` (canonical payload + base64 Ed25519 signature + key id + public PEM), then discards the private key; public keys stay plaintext so verification needs no password. `PersonSigningKey` keeps exactly one active key per person and retires (never deletes) old ones so historical signatures stay verifiable. `CryptoAuditLog` is an append-only, plaintext-free record. This app is the credential store for the rest of base — SSO signing keys, backup signing keys, API secrets and contract signatures all resolve to gervazy blobs. Depends on `people`.

#### vault — file storage

User-facing (unencrypted) file storage — distinct from gervazy's encrypted-at-rest store. Concepts: `Bucket` (named namespace with optional MB quota), `VaultDirectory` (hierarchical folders with per-user access), `VaultFile` (typed upload with content hash and optional public access), `FileGateway` (an upload endpoint bound to a directory) and `StorageProvider` (S3-compatible preset). The `storage_backend` selects `local` (default, under `MEDIA_ROOT`), `s3`, or `remote_toto` (proxies another toto instance). Exposes a `VaultEditorPlugin` extension point (see editor) and a `/vault/api/` JSON API (list/upload/detail/delete/download, auto-creating a `personal-<username>` bucket). Storage billing via `toto.metering.charge` is optional and non-fatal — the metering/tariffs apps live outside the standard build, so uploads proceed uncharged when they're absent. Seeded by `ingress_vault`.

#### editor — shared in-browser editors

A *feature* app (installed when `BUILD_LATEX` or `BUILD_PYEDITOR` resolves on), with no models. `BaseFileDisplayView` (and per-format subclasses for text/JSON/YAML/XML/HTML/CSV/LaTeX/BibTeX) renders an Ace editor over a `vault.VaultFile`, gated by `LoginRequiredMixin` and ownership, honouring encrypted-file locks and an optional gitvault toolbar. `save_file`/`delete_file` handle persistence. Live collaboration runs over Channels: `EditorFileSyncConsumer` (a `BaseFileSyncConsumer`) syncs file content across sessions using `diff_match_patch` patches, exposed at `ws/editor/file/<pk>/` via `routing.py` (collected by the host's `collect_websocket_urlpatterns`). It registers `VaultEditorPlugin` subclasses so vault's file listing offers the right editor per file type (the SVG editor deliberately lives in the sketch sibling, not here).

#### api — outbound connectors & email

`ApiConnector` (abstract) holds outbound-integration config: `base_url`, `auth_type`, JSON-schema-validated `auth_config` (which *rejects* secret-looking keys), plus FK references to `gervazy.EncryptedSecret` (`api_secret`) and `gervazy.EncryptedPrivateKey` (`signing_key`) — the config itself never stores plaintext secrets. `Connector` is the concrete subclass; the `steven` sibling subclasses `ApiConnector` for LLM providers. `EmailService` wraps SMTP config (host/port/TLS, `smtp_secret` FK) for community notification email. Depends on `gervazy`.

#### backup — signed archives

`BackupProfile` (OneToOne with `core.Platform`) holds a gervazy `signing_key` and a plaintext `verify_key` PEM. Outbound archives are signed before transmission; incoming ones are verified. `StoredBackup` records each archive (`uid`, platform, `FileField` at `stored-backups/{uid}/{filename}`, size, checksum, `is_verified`). Depends on `core`, `gervazy`.

#### quota — usage metering & limits

A generic, app-agnostic meter. `QuotaPolicy` defines a limit for an `(app_label, metric_code, subject)` tuple over a `period` (daily…lifetime) in a `mode` (`track`/`warn`/`block`); a subject-specific policy overrides the global one (empty subject). `UsageEvent` records one action, with an `idempotency_key` guarding against duplicates and a `voided` status. The `api.py` service layer (`get_policy`, `check_quota` → raises `QuotaExceeded`, best-effort `record_usage` that never raises, and `usage_summary` for per-subject dashboards) is what other apps call. Standalone.

#### verbena — content primitives

No concrete models, no routes — just three abstract bases (all extending `DomainEntity`) that every content app inherits via Django multi-table inheritance: `AbstractTag`, `AbstractPage` (titled/slugged rich-text doc), and `AbstractSection` (ordered content block with an optional `people.Person` author). `socialhub`'s news models and content apps in sibling packages (palimpsest, kanban docs, academy scripts, memo, library) subclass these. Depends on `people`.

#### SSO: moved to toto-auth

The three SSO apps (`sso_core`, `sso_master`, `sso_client`) and the
`toto.auth_config` login-strategy resolver ship in the sibling
[`toto-auth`](../toto-auth/README.md) package since 1.8. Import paths, app
labels and migrations are unchanged; base keeps the shared login
implementation (`core/auth_views.py`, used by both `core:login` and
`sso:login`) so it stays below `toto-auth` in the dependency graph.

### Dependencies

At the Python-distribution level, toto-base depends only on `Django>=4.2` and `PyYAML>=6.0` — it is the foundation, so it carries **no** sibling `toto-*` pins. The dependency direction runs the other way: the other nine suite packages pin `toto-base`.

---

## Usage

toto-base is a library of Django apps and host-API helpers; you consume it from a host project rather than running it directly.

**Install** (as part of a pinned suite — the normal path):

```bash
pip install --no-index --find-links dist -r requirements.toto.txt
```

`requirements.toto.txt` must list `toto-base==<version>` (plus any other suite packages the host needs) at one shared version; anything else is rejected at install and boot time.

**Compose a host's settings** using the host API instead of hand-listing apps:

```python
from toto.registry import BASE_APPS, FEATURE_APPS, has_app
from toto.features import resolve_features
import os

features = resolve_features(os.environ.get)
INSTALLED_APPS = [*django_and_third_party, *BASE_APPS]
if features.editor:
    INSTALLED_APPS += FEATURE_APPS["editor"]
```

Wire the rest from the same modules: `toto.routing.collect_websocket_urlpatterns()` in `asgi.py`, `toto.schedules.beat_schedule(...)` for `CELERY_BEAT_SCHEDULE`, `toto.conf.data_dir()/run_dir()` for filesystem paths, and add `toto.core.middleware.PlatformMiddleware` (and siblings) to `MIDDLEWARE`. Import shared template context via `from toto.ui import PageProcessor`.

**Migrate and seed:**

```bash
python manage.py migrate
python manage.py ingress_<app> [--full]   # e.g. ingress_vault, ingress_storage_providers
```

`toto.locations` requires a PostGIS-enabled PostgreSQL database. The SSO provider needs `SSO_VAULT_PASSWORD` available at runtime to unlock its signing key.

**Run the tests** for the apps that ship one (from a host that installs base):

```bash
python manage.py test toto.locations.tests_api toto.socialhub.tests_api toto.vault.tests_api
```

**Develop against a checkout:** work inside the `toto_libs` monorepo. Set `TOTO_SKIP_VERSION_CHECK=1` to run from an unpinned local checkout, and use the repo's `--dev` build path for local, unpinned wheels.

---

## Build & packaging

toto-base is one of the 9 lockstep-versioned wheels in the toto suite; all share the `toto.*` PEP 420 namespace and a single repository `VERSION` (currently `1.6`). Versions are rewritten only by `scripts/release.py` — never by hand — and built with `scripts/build_wheels.py`; `scripts/check_package_graph.py` keeps each package's slice of `src/toto/` disjoint. Package data ships templates, static assets and `graph/*.yaml`, while `download_vendor.py`-fetched vendor assets are excluded so wheels stay deterministic. Because it is the foundation, toto-base pins no sibling packages — the other eight pin it. Three layers enforce version coherence (build-time checkout/wheel verification, install-time pins, and the runtime check that `toto.core` runs at boot).

For the full build, release and pinning manual, see the repository root README.
