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

`monit.models.Snapshot` is the app's only model: one row per periodic
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

`monit.tasks` defines two Celery `shared_task`s:

- `monit_sample` (`soft_time_limit=55`, `time_limit=90`) — creates one
  `Snapshot` from `collect_all_for_snapshot()`. Runs in the worker container.
- `monit_prune` — deletes snapshots older than `MONIT_RETENTION_HOURS` (default
  48).

These are *not* scheduled by the package. The beat entries are owned by
`toto.schedules.beat_schedule(monit=..., monit_minutes=...)` in `toto-base`,
which registers `monit-sample` every `MONIT_SAMPLE_MINUTES` (default 2) and
`monit-prune` hourly at minute 17. Only the dedicated beat container reads the
schedule.

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
