# toto-base

**toto-base** is the foundation wheel of the toto suite: the one distribution every toto host installs. It carries the *shared host API* (the app lists, feature-flag resolution, ASGI/Celery wiring, filesystem config and version-coherence machinery that a host stitches into its Django settings) together with the irreducible cluster of platform apps that cannot be layered apart — platform identity and branding, people, geography, communities, events, the encryption vault, file storage, usage quotas, backups and the shared content/editor primitives (the OpenID Connect provider/consumer apps ship in the sibling `toto-auth` package). It is one of 10 lockstep-versioned distributions sharing the `toto.*` PEP 420 namespace; hosts pin every toto package to the same version in `requirements.toto.txt`.

---

## What it does (functional)

toto-base is what turns an empty Django project into a running toto platform. An operator who installs it gets:

- **A branded, single-tenant platform.** One `Platform` record defines this deployment's name, domain, contact address and visual identity — a theme built from a colour palette and body/heading fonts, with optional dark mode and custom CSS. The platform can be flipped into maintenance mode, and it belongs to a named `Federation` that groups related deployments.
- **People and communities.** Every human in the system is a `Person` — the shared identity anchor used everywhere, whether or not they have a login. People join `Community` groups through an email-verified application that requires a reference from an existing member. Communities have leaders and senior members, form parent/child hierarchies, publish news posts, and adopt a statute. A community can carry a privilege row that grants named rights to every one of its members — the only way rights are held on this platform, since the "Special Roles" that also granted them were removed in 8/2026.
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

The first app to boot. `Platform` is the singleton for this deployment (name, domain, contact, theme, active flag, rate-limit settings); `Federation` groups platforms into a network. Branding is `Theme` (a `ColorMix` palette + body/heading `Font`s, dark-mode and custom-CSS overrides). A `Font` row's stylesheet link (Google Fonts, for every seeded one) is drawn only while the host's `USE_EXTERNAL_FONTS` is on; off, the base template draws the platform's own copy instead (`toto.ui.page.LOCAL_FONTS`: Orbitron, beside its SIL Open Font License, 2026-10-01) or falls back to the font's style family, so no visitor's address reaches Google. `DomainEntity` is the abstract base almost every domain model inherits — it supplies `uuid`, `slug`, `name`, `description`, `logo`, `metadata`, `created_at`, `updated_at` without extra tables. `core` also owns middleware (`PlatformMiddleware` for maintenance-mode redirects and per-IP rate-limit bookkeeping; `ProfileLanguageMiddleware` for per-user locale; `ContentSecurityPolicyMiddleware` for an opt-in CSP header) and the always-available `graph_export` template tag (renders an "Export to graph" button only when the optional Neo4j layer is present). Its `AppConfig.ready()` runs the suite-wide version check and enables SQLite WAL mode. Standalone (no toto dependencies).

**The sign-in lockout** (`signin_lockout.py`, 2026-09-30) is core's too, because every password door calls `authenticate(request, …)`: the sign-in form (`auth_views.password_login_view`, behind `core:login` and `sso:login`), `/api/login/` and the admin login; sso_master's signup API, which ends in it, asks first and makes no account while paused. `SigninLockoutBackend`, first in `AUTHENTICATION_BACKENDS` (`toto.auth_config` puts it there), authenticates nobody and raises `PermissionDenied` while a sign-in is held, so no password is compared — the right one included; a `user_login_failed` receiver counts, a `user_logged_in` receiver clears. Counts are per (normalised name as typed + client address) and per address, in the cache through `ratelimit.py` (`count`/`hold`, fail open): after `LOGIN_DELAY_AFTER` (5) failures each try waits 2^(n−5) s up to `LOGIN_DELAY_MAX_SECONDS` (60), answered at once and never slept; after `LOGIN_LOCK_AFTER` (10) the pair is paused `LOGIN_LOCK_MINUTES` (15); after `LOGIN_ADDRESS_LOCK_AFTER` (50) from one address, any names, the address is; a count is forgotten `LOGIN_FAILURE_WINDOW_MINUTES` (15) after its last failure; 0 turns a rule off. **Never the account itself**: every pause names an address (so everybody behind one address shares its count — on an onion service that is every visitor, and such a host sets `LOGIN_ADDRESS_LOCK_AFTER = 0`). The refusal is one sentence whatever the name, so it says nothing about who exists; the doors show it (the form counts it down, the API answers 429 with `Retry-After`, the admin's login form — `admin_login.py`, set by `ready()` unless the host chose its own — names it). `authenticate()` without a request is neither counted nor refused. Pauses are lifted at the console only: `manage.py unlock_signin --user NAME | --address IP | --all`. On the audit chain as `AUTH.LOCKED` / `AUTH.UNLOCKED`. The 3-second session cooldown (`auth_cooldown.py`) still runs in front of the form. Tests that fail sign-ins on purpose clear the cache before and after, as `tests_signin_lockout.py` does: the counts live there, not in the database a test rolls back.

**The visitor's address** is `client_ip.py` (2026-09-30), said once: nginx's `X-Real-IP`, believed only when the peer is in `TRUSTED_PROXIES` (loopback when unset) or `TRUST_X_REAL_IP` is on; otherwise `REMOTE_ADDR`. `X-Forwarded-For` is never read. Every address the suite writes down comes from it: the audit chain's `request_source`, sso_master's pairing source, a vault peer grant's `last_peer_ip`, a company ballot's evidence, a transcript event's hashed address.

**What an error mail may carry** is `error_reports.py` (2026-10-01). A host that mails its operators Django's crash report (`ADMINS`, an `AdminEmailHandler` on `django.request`) names `PlatformExceptionReporterFilter` as `DEFAULT_EXCEPTION_REPORTER_FILTER` and `PlatformExceptionReporter` as `DEFAULT_EXCEPTION_REPORTER`: a setting, header or cookie whose name says secret is starred at any depth (Django's list plus AUTHORIZATION, COOKIE, CREDENTIAL, PRIVATE, SALT, DSN — so `FIELD_ENCRYPTION_KEY`, `*_SECRET`, `*_PASSWORD`, `*_KEY`, `*_TOKEN`), and so is a password inside a URL (`redis://:pw@…`); every cookie value and every POSTed value is starred, only the names kept, whatever `@sensitive_post_parameters` says; a GET parameter is shown unless its name says secret (`token`, `code`, `sig` …); and the exception's message, its causes and the request URL are scrubbed of every value starred elsewhere; a URL path that is a credential — every value a route in `SECRET_ROUTES` captures (the vault's peer routes: a grant's id and its magic token), and on any route one captured under a name that says secret — is starred wherever the mail shows the path, the subject included, for which the host's handler is `PlatformAdminEmailHandler` (2026-10-01, the review); logged inside a transaction, that handler mails after the commit. `CrashMailFilter`, on the handler, passes only a crash (a record carrying an exception — not a page answering 503 on purpose) and the same crash (type and lines of code) once an hour, counted in the shared cache, and per process when the cache cannot answer (Redis down: the first is mailed, its repeats are not). The host sends the plain-text report only (`include_html` False), so no frame's local variables leave the server. It leaves at once, from the request that crashed, and not through the worker like the notices below: the crash may be the broker's or the worker's own, a report queued behind them would never leave, and the request's details have no business waiting in Redis (the module docstring says it in full). zenobia wires it in `settings.py` (ADMINS from `ERROR_EMAILS`, else `ALERT_EMAILS`). Tests: `tests_error_reports.py`.

**Mail that must leave** is `checks.py` (2026-10-01): a host that sets `REQUIRE_SMTP` gets a system check that stops `manage.py check` — and every management command that runs the checks, a container's start among them — while its mail settings could not send: a backend that is not Django's SMTP backend or a subclass of it (core.E001), no mail server or Django's `localhost` (E002), neither STARTTLS nor implicit TLS (E003), a sender at localhost such as Django's `webmaster@localhost` or `root@localhost` (E004 `DEFAULT_FROM_EMAIL`, E005 `SERVER_EMAIL`), and no `EMAIL_TIMEOUT` (E006). Off by default; the password is never looked at — only a login against the real server proves it, which is the host's deploy's job (zenobia: `deploy.py` writes `REQUIRE_SMTP=1` for `deployment.environment: cloud` and runs `manage.py mail_check` once the stack is up). Tests: `tests_require_smtp.py`.

**Notices** (`notices.py`, 2026-09-30) are the short mails that tell a member something happened to their account — the password changed, a new sign-in, the e-mail change's confirmation link and its "your address was changed" — and, since 2026-10-01, the operators' alert mail (`check_alert`, `check_recovered`, sent by `toto.monit.alerts`). Every one leaves through `send_notice(user, kind, context, to=)`, which never raises. **On the worker, with retry** (2026-10-01): where the host says a Celery worker runs (`NOTICES_VIA_WORKER`; zenobia's deploy.py sets it with the worker) the notice is rendered in the caller — the member's language, the time of the event — and handed to `tasks.deliver_notice` on `transaction.on_commit`, so a change that rolls back announces nothing. The task tries `TRIES` (5) times, `RETRY_DELAYS` (2, 10, 20 and 30 minutes) apart, then gives up; it keeps that bound itself, because Celery's own "max retries exceeded" error prints the task's arguments into the log, and no wait is longer than half an hour, because Redis hands a retry left waiting past the broker's visibility timeout (35 minutes on zenobia) to a worker again and the mail would go twice. A broker that cannot be reached sends at once instead; with no worker the notice is sent at once, one try, as before. Each try's outcome lands on `NoticeDelivery`, one row per kind: sent, being retried or failed, the tries, the error's class and SMTP reply code (`error_text` — never the server's words, which can echo the address or the login), a keyed hash of the recipient (`recipient_hash`, an HMAC under `SECRET_KEY`), and `failures`, the sends that failed for good since a notice of any kind last went. Never the address, the subject or body, a link or the SMTP password: it is not an outbox (toto.jess kept one, and the password with it, and was retired for that). toto.monit's Mail check reads it. Tests: `tests_notices.py`, `tests_notice_delivery.py`.

**Personal data** (2026-10-01, RODO / GDPR) is core's where it spans every app. `personal_data.py` builds a person's copy of their data — a zip of CSV/JSON tables (profile, communities and clearances, membership applications and privacy notices accepted, plan and charges, ledger statement, events, forum messages, sessions, the audit records whose actor they are and, in a table of their own, those about them that somebody else wrote — an administrator, a referrer, the platform, whoever typed their name at the sign-in — without that side's address and browser (`toto.audit.queries.records_about`, 2026-10-01); an app the library cannot import adds its own through a `PersonalDataPlugin` in `<app>/plugins/personal_data_plugins.py`, as zenobia's wiki does), their own files and a README, never a password hash, key or token — behind both `manage.py export_user` (the console) and socialhub's *Download my data*. `manage.py erase_user` is the only eraser: console only, its `plan` (Django's deletion collector) is the report, and it closes the member's open erasure request in the same transaction. **The nightly housekeeping** (`housekeeping.py`, run by `tasks.nightly_housekeeping` — "toto.core" is in `registry.TASK_MODULES` for it — and scheduled by `schedules.beat_schedule(housekeeping=True)`, 03:05) runs Django's `clearsessions`, deletes the `UserSession` rows whose session is gone (`user_sessions.prune_dead`; before, they went only when the member opened their Sessions list) and, where the socialhub is installed, prunes the membership applications that lapsed more than `SOCIALHUB_EXPIRED_APPLICATION_DAYS` (30) days ago with the never-used accounts they made — never an account that got in or owns anything beyond what sign-up gives every account, which `erase_user`'s own `plan` proves (socialhub `applications.py`). Each step runs on its own, and each run is ONE `PRIVACY.HOUSEKEEPING` record on the audit chain with counts only — no address, username or e-mail — `success=False` and the failed step named when one failed. Tests: `tests_personal_data.py`, `tests_records_about_you.py`, `tests_erase_user.py`, `tests_housekeeping.py`.

**Modals keep focus** (`static/oya/focus_trap.js`, 2026-10-01): `x-trap="expression"` on a modal — named and shaped like Alpine's Focus plugin, which is not vendored — keeps Tab and Shift+Tab inside it while the expression is true (round at its ends; focus that leaves anyway is brought back), focuses what the modal focused itself, else an `[autofocus]` control, else its first control, and returns focus to the opener when it closes; traps stack, the newest counts. Escape stays each modal's own `@keydown.escape.window`. `oya/base.html` loads it without `defer`, because it registers the directive on `alpine:init`; `window.totoFocusTrap(el)` is the same trap without Alpine. The vault's Management, Trash and bulk dialogs and the socialhub's account, clearance, erasure and address modals wear it. Tests: `tests_focus_trap.py` (the script in node).

#### people — the identity anchor

`Person` (a `DomainEntity`) is linked one-to-one with an optional `auth.User` — people without login accounts can exist (referenced contacts, external parties). Every domain action FKs into `Person`, never the raw `User`, decoupling platform identity from Django auth. Key fields: `communities` (M2M to `socialhub.Community`), `patron` (self-FK mentor link), `address` (FK to `locations.Address`), `is_federal_agent` (gates responder eligibility in the mobilization sibling), and `digital_signature` (a base64 PNG of a handwritten signature — decorative, distinct from the cryptographic Ed25519 key gervazy manages). Depends on `locations`.

#### locations — GIS substrate

PostGIS models (SRID 4326) extending `DomainEntity`: `Address` (postal fields + `PointField`), `Territory` (`MultiPolygonField`), `Zone` (typed sub-area with a `PolygonField`), `Route` / `RouteChain` (typed `LineStringField` paths), and `MapLayer` / `MapLayerPolygon` (thematic overlays). Enables spatial queries used across the suite (detections in a zone, nearest inventory site, etc.). Ships a small public "Enigma" JSON API under `/locations/api/` for zones and addresses. Depends on `people`.

##### Map domains (2026-09-30)

Clearances go on groups, never on items. A **map domain** (`MapDomain`: name, slug, description) groups map items — routes, map layers, addresses, zones, territories, and whatever kind an installed app adds (the host's places) — through one typed through table per kind (`RouteInDomain`, `MapLayerInDomain`, `AddressInDomain`, `ZoneInDomain`, `TerritoryInDomain`; never a generic key), and is kept to clearances by `MapDomainClearance` rows (the clearance is PROTECTED). The rule is `socialhub.clearance_access.group_gate` (`locations/access.py`: `readable_routes`, `readable_layers`, `readable_addresses`, `readable_zones`, `readable_territories`, `may_read`, `domain_gate` for another app's kind): an item in no kept domain is every signed-in member's; an item in kept domains is read by superusers and by whoever holds, for EVERY kept domain of it, one of that domain's clearances (pessimistic) — not its creator, not a layer's owner, not staff. A hidden item is a missing one on every locations door: the map page (and a route chain, drawn and counted from its reader's readable routes), every detail page, the metadata and note saves, the route search's address pickers and the route save, the JSON API, the field-map features and counts, and the `locations_read` connector (which reads as nobody: the open items only). Addresses are gated on the locations doors only: a person's profile, a community and an event show an address by their own rules. The per-item `RouteClearance` / `MapLayerClearance` and the detail page's "Who sees this" went (2026-09-30).

Superusers manage domains on the **Domains** tab of the Locations strip (`/locations/domains/`, `domain_views.py`; 403 for anyone else, and it works on a GIS-off build): a paginated table (cards on a narrow screen) of each domain's clearances, item counts per kind and date, with modals for **New domain** (name, description, clearances, first items), **Items** (pick a kind, search by name, click to add; take items out), **Clearances** and **Delete**. A refused New domain comes back through the session (Post/Redirect/Get). Every change is on the audit chain: `LOCATIONS.DOMAIN.CREATED`, `DELETED`, `ITEM_ADDED` / `ITEM_REMOVED` (kind, item) and `CLEARANCES_CHANGED`. The kinds are a plugin point (`plugins/domain_plugins.py`, `MapDomainKind`, autodiscovered from every app's `plugins/domain_plugins.py`): a kind names its model, its through table and how it is searched. Every seeded platform has one domain: `ingress_locations` (realistic and full modes, never `none`, GIS or not) makes `regulated_domain` ("Map items a superuser may keep to clearances.") with no clearances and no items, once — a re-run never touches it, so a superuser's edits survive. Tests: `tests_clearances`, `tests_more_clearances`, `tests_domains`, `tests_more_ingress`.

#### socialhub — communities & membership

`Community` (a `DomainEntity`) is the primary grouping unit: it has a `federation`, an `org_type`, hierarchy (`parent` self-FK), a `head` and `senior_members`, and a headquarters `location`/`territory`. It used to carry an `email_service` FK for a per-community mail relay; that went away with `api.EmailService` in 1.25 (it never delivered a message), and endorsement mail now goes out through `jess` with the community's own address as `Reply-To`. Flags like `is_autonomous`, `is_foreign` and `is_federal_tribe` (elevates a community so members become emergency-responder-eligible) drive behaviour elsewhere. Membership flows through `MembershipApplication` (a 6-digit code the applicant retypes from a CAPTCHA — it is never mailed) plus a `ReferenceRequest` from an existing member. Since 2026-10-01 (RODO) the application requires accepting the current `PrivacyNotice` (versioned, public, a placeholder text until the organisation writes its own), recorded as a `PrivacyAcceptance` on admission; one that lapses is renewed when its applicant applies again and pruned by core's nightly housekeeping 30 days on; and My account offers *Download my data* (`DataExport`) and *Erase my account* (an `ErasureRequest` the console's `erase_user` carries out) — see `toto/socialhub/README.md`. Communities publish `CommunityNewsPost`/`CommunityNewsTopic` (built on verbena's section/tag bases) and adopt a `Constitution` that members endorse via `ConstitutionSignature`. Public `/socialhub/api/` profile and community endpoints. Depends on `api`, `locations`, `people`.

#### events — scheduling & availability

`EventBase` (abstract, extends `DomainEntity`) carries `category`, `title`, time window, `is_public`/`is_cancelled` — it is the parent of both `ScheduledEvent` (concrete: owner/organizers as `people.Person`, venue `locations.Address`, capacity, `socialhub.Community`) and, in a sibling, `detections.Detection` (time-anchored events reusing the same fields). `EventInvite` tracks RSVPs; `Availability` records a person's free/blocking windows (with JSON recurrence). Depends on `locations`, `people`.

#### gervazy — encryption-at-rest & signing

Implements a three-tier AES-256-GCM key hierarchy: a user password runs through Argon2id to derive a UKEK (never stored) → unwraps a per-user `VaultMasterKey` → wraps namespace-scoped `WrappedDataKey`s → those DEKs encrypt `EncryptedSecret`, `EncryptedFile` (+ sequential `EncryptedFileChunk`) and `EncryptedPrivateKey` blobs. Decryption requires the user's password at runtime. A separate Ed25519 signing layer (`signing.py`) stores each person's private key encrypted in their `UserStrongbox` and exposes a stateless `SigningService` (`provision_signing_key`, `sign_document`, `verify`, `canonical_contract_payload`) that opens a short-lived `GervazyCryptoSession`, produces a `DocumentSignature` (canonical payload + base64 Ed25519 signature + key id + public PEM), then discards the private key; public keys stay plaintext so verification needs no password. `PersonSigningKey` keeps exactly one active key per person and retires (never deletes) old ones so historical signatures stay verifiable. `CryptoAuditLog` is an append-only, plaintext-free record. This app is the credential store for the rest of base — SSO signing keys, backup signing keys, API secrets and contract signatures all resolve to gervazy blobs. Depends on `people`.

#### vault — file storage

User-facing (unencrypted) file storage — distinct from gervazy's encrypted-at-rest store. Concepts: `Bucket` (named namespace with optional MB quota), `VaultDirectory` (hierarchical folders with per-user access), `VaultFile` (typed upload with content hash and optional public access), `FileGateway` (an upload endpoint bound to a directory) and `StorageProvider` (S3-compatible preset). The `storage_backend` selects `local` (default, under `MEDIA_ROOT`), `s3`, or `remote_toto` (proxies another toto instance). Exposes a `VaultEditorPlugin` extension point (see editor) and a `/vault/api/` JSON API (list/upload/detail/delete/download, auto-creating a `personal-<username>` bucket). Storage billing via `toto.metering.charge` is optional and non-fatal — the metering/tariffs apps live outside the standard build, so uploads proceed uncharged when they're absent. Seeded by `ingress_vault`.

#### editor — shared in-browser editors

A *feature* app (installed when `BUILD_LATEX` or `BUILD_PYEDITOR` resolves on), with no models. `BaseFileDisplayView` (and per-format subclasses for text/JSON/YAML/XML/HTML/CSV/LaTeX/BibTeX) renders an Ace editor over a `vault.VaultFile`, gated by `LoginRequiredMixin` and ownership, honouring encrypted-file locks and an optional git toolbar. `save_file`/`delete_file` handle persistence. Live collaboration runs over Channels: `EditorFileSyncConsumer` (a `BaseFileSyncConsumer`) syncs file content across sessions using `diff_match_patch` patches, exposed at `ws/editor/file/<pk>/` via `routing.py` (collected by the host's `collect_websocket_urlpatterns`). It registers `VaultEditorPlugin` subclasses so vault's file listing offers the right editor per file type (the SVG editor deliberately lives in the sketch sibling, not here).

#### api — outbound connectors & email

`ApiConnector` (abstract) holds outbound-integration config: `base_url`, `auth_type`, JSON-schema-validated `auth_config` (which *rejects* secret-looking keys), plus FK references to `gervazy.EncryptedSecret` (`api_secret`) and `gervazy.EncryptedPrivateKey` (`signing_key`) — the config itself never stores plaintext secrets. `Connector` is the concrete subclass; the `steven` sibling subclasses `ApiConnector` for LLM providers. It no longer holds an `EmailService`: that model was absorbed into `jess` in 1.25 and its table dropped — see that section. Depends on `gervazy`.

**The token doors** (`tokens.py`, `middleware.py`). The desktop client's token is the session key `/api/login/` hands out. It is presented as `Authorization: Bearer` to the JSON doors (`cors.py`) and to the WebSocket in the subprotocol header (2026-10-01): the client offers `toto.bearer` and then the key, and `TokenAuthMiddleware` answers `toto.bearer`, never the key, and hands the consumer the offer without it; today's desktop clients still send `?token=`, which works as before. Both doors check it as a cookie is checked (`tokens.user_for_session_key`, 2026-09-30): the backend's `get_user` and the session hash; a refused token is as anonymous as none, and `AUTH.TOKEN_REFUSED` goes on the chain without the key. A URL is what logs keep, so `server_logs.py` cuts the query string off uvicorn's own lines when the middleware is built (nginx's access log is the host's deploy.py). Tests: `tests/test_token_resolution.py`, `tests/test_socket_subprotocol.py`.

#### jess — platform email transport

A *feature* app (`BUILD_JESS`), named after Postman Pat's cat — see `src/toto/jess/README.md` for the tribute and the reasoning it carries. It owns every email the platform sends. `EmailProvider` is the transport, configured in the admin rather than the environment: a backend choice (`smtp`/`console`/`dummy`/`locmem`/`filebased`), SMTP host/port/TLS/timeout, a separate SMTP `username` and `from_address`, and the password as a `gervazy.EncryptedSecret` under **Jess's own** `jess-system` strongbox with its own `JESS_VAULT_PASSWORD`. Several rows may exist and exactly one is `active`, enforced in `save()` under a transaction — so a console row can sit beside a working SMTP row, and a candidate can be proven with the admin's "send test" action *before* it is switched on.

`EMAIL_BACKEND = "toto.jess.backend.JessEmailBackend"` puts the queue boundary in the backend, so no caller can bypass it: every send writes one `MailMessage` row (the outbox — recipients as JSON lists, both bodies, headers, `purpose`, `attempts`, the provider's error kept verbatim, `queued/sending/sent/failed`) and hands it to `jess/tasks.py::send_mail_message`. A request therefore never blocks on SMTP, and a broker outage becomes a queue of visible rows rather than lost mail. Two deliberate semantic changes follow: `send_messages()` returns messages *accepted*, not delivered, and `mail.outbox` no longer fills — which is safe only because the reset suites `override_settings(EMAIL_BACKEND=locmem)`, and `jess/tests.py` asserts that rather than trusting it. There are **no automatic retries**: a failed row keeps its error and waits for a human, because a silent backoff hides a misconfigured mail server for hours.

`status.can_deliver()` is what makes `core.email_config.email_delivery_configured()` — and so the "Forgot password?" link — tell the truth: it is True only for an active delivering provider whose password is actually readable, and it opens no socket, because a reachability probe on a login page render would put a third party's latency on every anonymous request.

Staff pages at `/jess/`: an outbox, a compose page that sends through the *same* path as everything else, and a message detail that polls and shows the provider's error verbatim. The gate raises `PermissionDenied` (**403**, not a redirect) so the poller can act on it. Both models are `IDENTITY_REFUSE` in datalink: a provider's ciphertext cannot decrypt on a peer, and an outbox records who was emailed what.

Deliberately not modelled yet, so nobody thinks it was forgotten: no templates, no scheduling, no recipient lists, no bounce ingestion, no metering. `purpose` and `provider` are the two hooks that make all of those additive. Metering in particular is omitted on purpose — `quota/apps.py` imports `<app>.metrics` for every installed app at startup, and charging a user for a password reset they did not request is the wrong meter. Depends on `gervazy`, `core`.

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
