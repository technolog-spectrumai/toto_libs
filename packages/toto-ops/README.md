# toto-ops

`toto-ops` is the operations distribution of the toto suite: a single, read-only
monitoring dashboard (`toto.monit`) that gives operators a lightweight,
self-hosted alternative to Grafana. It periodically samples the health of the
running deployment — system resources, backing services, and web-tier traffic —
stores the samples in the app's own database, and renders a superuser-only
overview page with live readings and 48-hour trend charts, plus a public
health-check endpoint. It ships as one of nine lockstep-versioned wheels that
share the `toto.*` PEP 420 namespace and is pinned by host projects in
`requirements.toto.txt`.

## What it does (functional)

`toto-ops` adds a **Monitoring** dashboard to a toto host so an operator can
answer "how is the server doing right now, and how has it been doing lately?"
without standing up a separate metrics stack.

- **At-a-glance health.** A superuser-only overview page shows a live panel
  measured on the spot (CPU, memory, disk, database and Redis reachability and
  latency, Celery worker count, Tor/onion publication state, connected device
  counts) alongside trend charts covering the last 48 hours.
- **Trends over time.** Charts track CPU, host load, memory (sampler and web
  worker), disk usage against capacity, service latencies (database, Redis, web
  `/metrics`), and request/error rates per minute — so you can spot a slow leak,
  a restart, or a latency creep.
- **Only shows what the host runs.** The dashboard adapts to the deployment:
  Redis, Celery, Tor/onion, device, and web-scrape panels appear only when the
  corresponding service is actually configured or installed. A check that can't
  be run reads as "unknown," never as a misleading zero.
- **Told when something breaks.** The record checks behind the Database
  page — database, migrations, media store, disk, backups, audit chain and the
  platform's own TLS certificate — run on a schedule, and the operators in
  `ALERT_EMAILS` get a mail when one goes bad, a reminder while it keeps
  failing, and a mail when it recovers. Mail sent by the server cannot report
  the server itself being down; that needs something outside it.
- **Every scheduled task, on time or not.** Each run of a task the beat
  schedule names is recorded — when it started and ended, whether it
  succeeded, a short summary of what it returned — and one of those checks
  flags an entry that has not run for twice its cadence, and a beat that has
  stopped altogether. The Jobs page shows each entry's last run.
- **Public health check.** A minimal, detail-free `health/` endpoint returns
  `{"status": "ok"}` (HTTP 200) or an error (HTTP 503) after a database probe —
  safe to expose (including over Tor) for uptime monitors and container health
  checks.
- **Hands-off history.** Samples are collected automatically on a schedule and
  old ones are pruned automatically, so the dashboard stays current without
  operator intervention. History can also be browsed read-only in Django admin.

It is deliberately read-only: it observes and reports, and never changes the
state of the system it monitors.

## How it works (technical)

`toto-ops` contains a single Django app, **`toto.monit`** (`AppConfig.name =
"toto.monit"`, verbose name "Monitoring"). The app is intended to run inside a
host that also runs a Celery worker/beat (it is enabled on faros and on
zenobia's data-tier profiles).

### Data model — `Snapshot`

`monit.models.Snapshot` is the history model (`CheckState` keeps the
scheduled checks' last verdicts, `TaskRun` and `BeatEntry` the scheduled
tasks' heartbeats — see below): one row per periodic
measurement of the running deployment, ordered newest-first (`get_latest_by =
"created"`, `created` is `db_index`ed). Every value field is nullable, and
**NULL means "unknown/skipped," never zero.** Fields are grouped by *where* they
are measured:

- `sys_*` — the container/process running the sampler task (CPU %, host
  `load_1m`, memory used/limit, disk used/total).
- `db_*`, `redis_*`, `celery_*`, `tor_*`, `onion_*`/`clearnet_*`, `aster_*` —
  network-level service health, container-agnostic (reachability, latency,
  worker counts, onion publication state, device totals).
- `web_*` — scraped from the web tier's `/metrics` endpoint. Counters come from
  exactly one web worker process per scrape; `web_process_start` identifies
  which process, so rate charts only pair samples from the same worker.

The initial migration (`0001_initial`) creates this table with no cross-app FKs.

### Collectors

`monit.collectors` holds one measurement function per concern; each returns a
dict of `Snapshot` field kwargs, and **each individual read is guarded** so a
failing probe yields `None` rather than raising:

- `collect_system(cpu_window)` — reads cgroup v2 (`cpu.stat`, `memory.current`,
  `memory.max`) with a cgroup v1 fallback and a bare-metal `/proc/meminfo` /
  `/proc/loadavg` fallback; disk via `shutil.disk_usage("/")`. CPU % is derived
  from two cumulative-usage reads separated by `cpu_window`, normalized by core
  count.
- `collect_db` — times a `SELECT 1` on the default connection.
- `collect_redis` — uses the `django_redis` connection if the default cache is
  Redis-backed, else `MONIT_REDIS_URL`; records ping latency, used memory, and
  client count.
- `collect_celery` — pings workers via `celery.current_app.control.ping`.
- `collect_tor` — only if `toto.nomad` is installed; reads reachability and
  current onion via `toto.nomad.service`, plus a raw socket probe of the Tor
  control port.
- `collect_aster` — only if `toto.aster` is installed; counts total and
  recently-seen `AsterDevice` rows (freshness window `MONIT_ASTER_FRESH_HOURS`,
  default 24).
- `collect_web` — HTTP-scrapes `MONIT_WEB_METRICS_URL` (forcing the `Host`
  header to `MONIT_WEB_METRICS_HOST`, default `localhost`) and parses the
  Prometheus exposition for process start time, RSS, CPU seconds, total
  requests, and 5xx responses.
- `collect_request_metrics` — reads *this* process's in-memory
  `prometheus_client` registry for the live panel (uptime, RSS, request totals
  by status class, exception count, and the slowest views by request count).

`collect_all_for_snapshot` merges every snapshot-bound collector (all but
`collect_request_metrics`), isolating each so one failure can't abort the
sample. Third-party packages (`redis`, `requests`, `prometheus_client`) and the
optional sibling apps are all imported **lazily** — the package adds no runtime
dependencies of its own beyond `toto-base`.

### Scheduled tasks

`monit.tasks` defines three Celery `shared_task`s:

- `monit_sample` (`soft_time_limit=55`, `time_limit=90`) — creates one
  `Snapshot` from `collect_all_for_snapshot()`. Runs in the worker container.
- `monit_prune` — deletes snapshots older than `MONIT_RETENTION_HOURS` (default
  48), and run records older than `MONIT_RUN_RETENTION_DAYS` (default 30) but
  each task's newest.
- `monit_alert_checks` (`soft_time_limit=240`, `time_limit=280`) — the
  scheduled checks and their mail; see the next section.

These are *not* scheduled by the package. The beat entries are owned by
`toto.schedules.beat_schedule(monit=..., monit_minutes=...)` in `toto-base`,
which registers `monit-sample` every `MONIT_SAMPLE_MINUTES` (default 2) and
`monit-prune` hourly at minute 17, and with `alerts=True` `monit-alert-checks`
every `alerts_minutes` (default 5). Only the dedicated beat container reads the
schedule.

### Scheduled checks and alert mail (2026-10-01)

`monit.record` holds the record checks the Database page (`monit:status`)
runs on request; each returns a `Check` (OK, WARN, FAIL, UNKNOWN when the probe
itself failed, OFF when this host does not run it) and never raises.
`check_certificate` is one of them: a TLS handshake with the platform's own
public name — `MONIT_CERT_DOMAIN` when the host defines it (an empty value
means "not checked"), else `PLATFORM_DOMAIN` — reading the certificate's
notAfter: WARN under 21 days left, FAIL under 7. The handshake does not verify,
so a self-signed or an expired certificate is read too; `cryptography` is
imported lazily, and without it the check is OFF. Localhost, an address
or a local-only name is no public domain, and the check is OFF ("Not checked").

`monit.tasks.monit_alert_checks`, every `ALERT_CHECK_MINUTES` (beat entry
`monit-alert-checks` from `toto.schedules.beat_schedule(alerts=True,
alerts_minutes=...)`), runs the same checks in the worker and keeps each one's
last verdict in `CheckState` (one row per check: status and since when, when
it last went bad, what was mailed and when). `monit.alerts` decides what a run
owes the operators:

- a check that goes bad (WARN, FAIL or UNKNOWN) is mailed once;
- one that stays FAILING is mailed again every `ALERT_REMIND_HOURS` (0: never);
  a warning is said once;
- one that gets worse is mailed again, one that gets better without coming
  back is not;
- one that comes back (OK or OFF) is mailed once more, as recovered.

Every mail goes through `toto.core.notices.send_notice` (kinds `check_alert`
and `check_recovered`), one per address in `ALERT_EMAILS`. A mail no address
took is not counted, so the next run tries again; with no address nothing is
mailed and the states are still kept.

**What it cannot do.** The mail is sent by the server. No power, no network, a
stopped worker or beat, a database that cannot be reached (the states live in
it): each ends in silence, not in a mail — only something outside the server
can notice those.

### Heartbeats and run records (2026-10-01)

`monit.heartbeats` records every run of a task the beat schedule
(`CELERY_BEAT_SCHEDULE`) names, whoever sent it, from Celery's own signals —
connected in `MonitConfig.ready()`, so no task has to do anything:

- `task_prerun` writes a `TaskRun` (task name, Celery task id, `running`,
  `started_at`); `task_postrun` its outcome (`success`, `failed`, `retry`, or
  Celery's own state) and `finished_at`, with a `summary` of the return value
  — one line of at most 300 characters, empty values left out, a value under
  a key that names a secret starred, and so is every secret setting's value,
  a URL's password and a secret URL parameter (the rules of
  `toto.core.error_reports`) — or the exception's type and message, scrubbed
  the same way, in `error`. `task_failure` closes a run the worker process
  saw fail (a child killed by the hard time limit never reaches its own
  postrun); a run nothing closed — the worker container stopped mid-run — is
  closed as failed after six hours by the stuck-run sweeper (`sweeps.py`,
  `toto.quota.sweeps`). A retry reuses its row (one per task id). The
  receivers never raise; a write that fails is logged and the task runs on.
- `beat_init` writes a `BeatEntry` per schedule entry: `first_seen`, when beat
  first started with it, and `last_seen`, its latest start.
- The cadence comes from the schedule itself (`cadence_seconds`): an
  interval is its length, a crontab the widest gap between two firings
  (`*/7` is seven minutes; `hour="9-17"` the sixteen hours overnight;
  weekdays only, the weekend).
- `record.check_overdue` ("Scheduled tasks", key `overdue`, so the scheduled
  checks and their mail pick it up): an entry not started within twice its
  cadence plus ten minutes is WARN, three times FAIL. A failed run still
  counts as a heartbeat — the run happened. An entry that never ran is
  counted from its `first_seen` (a run from before it was scheduled does not
  count against it), else from when this host began recording (the
  migration `monit/0003`). When nothing scheduled has started within the
  most frequent entry's window, the summary says beat is not running or no
  worker takes its tasks, and names beat's last start. Two entries naming
  one task share its heartbeat.
- The Jobs page shows a table of the entries — cadence, last start, outcome
  and summary, on time or overdue — and lists the runs (source "Scheduled
  tasks") and the faucet payouts' and mana refills' `assets.FaucetRun` rows
  (source "Faucet runs", a status read off their counts).

So the daily levy and the billing, which keep no run table of their own,
have one now. **The blind spot** is the alert mail's: the alert run is itself
a beat entry on the same worker, so a dead beat or worker stops it too. The
Database page shows it to whoever looks, and the first alert run after beat
comes back mails the entries still overdue.

### Views, access control, and rendering

`monit.urls` (`app_name = "monit"`) exposes two routes:

- `""` → `OverviewView` (`monit:overview`) — a `TemplateView` gated by
  `MonitAccessMixin`, which raises `PermissionDenied` for anyone who is not an
  authenticated superuser. It builds the live panel by calling the collectors
  in-process for the current request, then loads up to 48 hours of snapshots,
  downsamples them to at most 180 points, and emits Chart.js line-chart JSON for
  each metric. Web request/5xx rates are computed as per-minute deltas between
  consecutive snapshots **of the same worker** (paired on `web_process_start`,
  with `max(0, delta)` to survive counter resets), so a restart shows a gap
  rather than a spike. Context is finalized through `toto.ui.PageProcessor`, and
  panels are conditionally populated based on what the host runs
  (`has_nomad`, `has_aster`, `has_prometheus`, `celery_configured`,
  `redis_configured`, `web_scrape_enabled`). Chart colors carry a light and a
  dark variant swapped client-side.
- `"health/"` → `HealthView` (`monit:health`) — a public `View` that runs
  `SELECT 1` and returns `{"status": "ok"}` / 200 or `{"status": "error"}` /
  503, always with `Cache-Control: no-store` and no internal detail.

Templates live under `templates/monit/` (`overview.html` plus `_stat_card` and
`_service_pill` partials) and are shipped as package data.

`monit.admin.SnapshotAdmin` registers `Snapshot` for read-only browsing
(date hierarchy, status filters) and explicitly denies add/change/delete —
retention is `monit_prune`'s job.

### Couplings and design decisions

- **Depends only on `toto-base`** (`toto.ui.PageProcessor`, the
  `toto.celery_utils` `current_app` idiom, and the schedule/feature/registry
  wiring in `toto.schedules`, `toto.features`, `toto.registry`).
- **Soft, optional couplings** to two faros-owned siblings — `toto.nomad`
  (Tor/onion state) and `toto.aster` (device counts) — are reached only through
  `apps.is_installed(...)` guards and lazy imports, so `toto.monit` runs cleanly
  on hosts that lack them. (`aster` and `nomad` themselves used to live in this
  package but were seceded to faros ownership.)
- **`django_prometheus`** (host-provided) is the source of both the scraped
  web-tier counters and the live in-process request metrics.
- Enablement is host-driven: the `monit` feature flag (`BUILD_MONIT`) maps via
  `toto.registry` to `["toto.monit"]`; hosts add it to `INSTALLED_APPS`, include
  its URLs, and expose a superuser-visible "Monitoring" menu entry.

## Usage

`toto-ops` is a library wheel consumed by a toto host project, not an
application you run on its own.

### Install

It is pinned alongside its siblings in the host's `requirements.toto.txt`:

```
toto-ops==1.6
```

Because the whole suite is version-locked, install it together with the matching
`toto-base==1.6` (and any other siblings the host uses).

### Enable in a host

1. Turn on the feature flag so the app is registered (e.g. `BUILD_MONIT=1`,
   which resolves through `toto.features` / `toto.registry` to
   `"toto.monit"` in `INSTALLED_APPS`).
2. Include the URLs, e.g. `path("monit/", include("toto.monit.urls"))`.
3. Run migrations: `python manage.py migrate monit`.
4. Run a Celery worker (to execute `monit_sample`/`monit_prune`) and a Celery
   beat container using `toto.schedules.beat_schedule(monit=True, ...)` so
   sampling and pruning happen automatically.

Relevant host settings the app reads (all optional; unset simply disables the
corresponding panel):

- `MONIT_WEB_METRICS_URL` / `MONIT_WEB_METRICS_HOST` — enable and target the
  web-tier `/metrics` scrape (empty URL disables it; the `Host` header defaults
  to `localhost`).
- `MONIT_REDIS_URL` — Redis endpoint when the default cache is not
  `django_redis`.
- `MONIT_RETENTION_HOURS` (default 48), `MONIT_SAMPLE_MINUTES` (default 2),
  `MONIT_ASTER_FRESH_HOURS` (default 24).
- `ALERT_EMAILS` (a list, or one comma-separated string; empty: no mail),
  `ALERT_CHECK_MINUTES` (default 5, read by the host's beat schedule),
  `ALERT_REMIND_HOURS` (default 6; 0: no reminders) — the scheduled checks.
- `MONIT_CERT_DOMAIN` (the name whose certificate is checked; unset:
  `PLATFORM_DOMAIN`; empty: not checked).
- `MONIT_RUN_RETENTION_DAYS` (default 30) — how long the scheduled tasks' run
  records are kept; each task's newest is kept whatever its age.
- `NOMAD_TOR_CONTROL_HOST` / `NOMAD_TOR_CONTROL_PORT` — for the Tor control-port
  probe when `toto.nomad` is present.

### Use

Once enabled, superusers reach the dashboard at `monit/` and any monitor can hit
`monit/health/`. Import points follow the namespace, e.g.:

```python
from toto.monit.collectors import collect_all_for_snapshot
from toto.monit.models import Snapshot
```

### Develop / test

Tests run from a host that provides the settings and the `toto.core` platform
model:

```
python manage.py test toto.monit
```

## Build & packaging

`toto-ops` is one wheel in the lockstep-versioned toto suite. All nine
distributions share a single VERSION (currently **1.6**) and pin their siblings
exactly; here that is the sole dependency `toto-base==1.6`. Version strings are
rewritten only by `scripts/release.py` and must never be edited by hand.
`scripts/check_package_graph.py` enforces that each package owns a disjoint
slice of the `toto.*` namespace (this one owns `toto.monit`). Package data
(`templates/**/*`, `static/**/*`, `graph/*.yaml`) is declared in
`pyproject.toml`. For the full build, versioning, and release process, see the
suite's root README.
