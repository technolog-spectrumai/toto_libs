# toto-geo

Everything with geography in it, for the **toto** platform. This distribution ships two apps. **locations** is the map: addresses as points, territories, zones, routes, map layers, map domains, and the links from people, events and communities to them; it moved here from `toto-base` on 2026-10-04 with every geometry-bearing piece, so `toto-base` needs no GIS library. **weather** fetches current conditions and hourly forecasts for the map's geocoded addresses, stores them as observations and forecast sessions, and surfaces them on the map. It is one of sixteen lockstep-versioned wheels that share the `toto.*` PEP 420 namespace and is versioned and pinned together with its siblings. zenobia, the main host, does not pin it and has no map.

## What it does (functional)

`toto-geo` gives a toto deployment its map (`toto.locations`, described under *How it works*) and adds weather awareness to the places on it. The weather app does this:

- **Current conditions on demand.** From the Weather page an operator picks one or more mapped locations and refreshes them. Each refresh records the temperature, precipitation (amount and type), cloud cover, wind speed and direction, and visibility observed at that location, along with the time it was observed.
- **Hourly forecasts.** For a selected set of locations and an optional start/end window, the app loads an hourly forecast and stores it as a single dated session of per-hour, per-location points (same measurements as the observations).
- **Automatic background refresh.** On a scheduled interval the platform can refresh current conditions for every geocoded location without any manual action, so the latest weather is always on hand.
- **Weather on the map.** Latest temperature and precipitation are exposed as colored map overlay layers, and each monitored location appears as a weather map feature with its most recent readings. When the tactical "Field Command" app is installed, weather also contributes a metrics panel (observation count, covered locations, forecast sessions).
- **Export.** Selected weather layers (temperature, precipitation) can be exported to a Vault file as GeoJSON, optionally into a specific bucket/directory, with an optional password to encrypt the result. The export returns a download link when it finishes.
- **Simple configuration.** An operator chooses which provider supplies current conditions and which supplies forecasts. Out of the box both use the free **Open-Meteo** service, which needs no API key.

Weather is switched on by `BUILD_WEATHER`, which defaults to the realtime tier (`BUILD_REALTIME`, still read under its old name `BUILD_STUDIO`) and is refused without `BUILD_GEO=1` (`toto.features`).

## How it works (technical)

`toto-geo` contains two Django apps under the shared namespace: `toto.locations` and `toto.weather` (`AppConfig` name `toto.weather`, verbose name "Weather").

### App: `toto.locations`

The map. It was an app of `toto-base` until 2026-10-04 and moved here with every geometry-bearing piece (this section moved with it, from that package's README); it kept its import path, app label (`locations`), URL namespace and migrations. A host that wants it pins `toto-geo` and adds `toto.registry.LOCATIONS_APPS` after `CORE_APPS` — it is never part of `CORE_APPS` or `BASE_APPS`.

GIS models (SRID 4326) extending `DomainEntity`: `Address` (postal fields + `PointField`), `Territory` (`MultiPolygonField`), `Zone` (typed sub-area with a `PolygonField`), `Route` / `RouteChain` (typed `LineStringField` paths), and `MapLayer` / `MapLayerPolygon` (thematic overlays). Enables spatial queries used across the suite (detections in a zone, nearest inventory site, etc.). Ships a small JSON API under `/locations/api/` for zones, addresses, map data, layers and route search.

**With or without GIS.** A host sets `HAS_GIS` (from `Features.geo`, `BUILD_GEO`; on by default). On, the app needs `django.contrib.gis`, GDAL and a spatial database (PostGIS, or SpatiaLite in the tests). Off, it loads without GeoDjango, its models drop their geometry columns, the host routes it to the second migration graph (`MIGRATION_MODULES = {"locations": "toto.locations.migrations_nogis"}`), and every map page answers 404 while its URL names still reverse; geocoding and map domains work on either build. The two graphs carry the same file names and are kept in step by hand; `tests/test_django_check.py` runs `makemigrations --check` in both modes.

**The links to the rest of the platform** (2026-10-04). In `toto-base` a person's address, an event's place and a community's seat are text. What ties them to the map lives here, one row each, and a host without this app has none of them: `Home` (a person's address on the map and its `HomeSharing`: off, approximate or exact — off by default; the rule is `people_access.py`), `EventPlace` (the map address an event takes place at) and `CommunitySeat` (a community's seat and the territory it covers). The home picker, the place search and the sharing setting arrive on the owner's profile as a profile plugin (`plugins/profile_plugins.py`, *Where you live*, on the Overview) posting to this app's own doors (`home_views.py`: `locations:set_my_home`, `search_home`, `set_home_sharing`); until 2026-10-04 they were the socialhub's.

#### Map domains (2026-09-30)

Clearances go on groups, never on items. A **map domain** (`MapDomain`: name, slug, description) groups map items — routes, map layers, addresses, zones, territories, and whatever kind an installed app adds — through one typed through table per kind (`RouteInDomain`, `MapLayerInDomain`, `AddressInDomain`, `ZoneInDomain`, `TerritoryInDomain`; never a generic key), and is kept to clearances by `MapDomainClearance` rows (the clearance is PROTECTED). The rule is `socialhub.clearance_access.group_gate` (`locations/access.py`: `readable_routes`, `readable_layers`, `readable_addresses`, `readable_zones`, `readable_territories`, `may_read`, `domain_gate` for another app's kind): an item in no kept domain is every signed-in member's; an item in kept domains is read by superusers and by whoever holds, for EVERY kept domain of it, one of that domain's clearances (pessimistic) — not its creator, not a layer's owner, not staff. A hidden item is a missing one on every locations door: the map page (and a route chain, drawn and counted from its reader's readable routes), every detail page, the metadata and note saves, the route search's address pickers and the route save, the JSON API, the field-map features and counts, and the `locations_read` connector (which reads as nobody: the open items only). Map addresses are gated on the locations doors only: the address a person's profile, a community or an event shows is its own text (`toto-base`), by that app's own rules. The per-item `RouteClearance` / `MapLayerClearance` and the detail page's "Who sees this" went (2026-09-30).

Superusers manage domains on the **Domains** tab of the Locations strip (`/locations/domains/`, `domain_views.py`; 403 for anyone else, and it works on a GIS-off build): a paginated table (cards on a narrow screen) of each domain's clearances, item counts per kind and date, with modals for **New domain** (name, description, clearances, first items), **Items** (pick a kind, search by name, click to add; take items out), **Clearances** and **Delete**. A refused New domain comes back through the session (Post/Redirect/Get). Every change is on the audit chain: `LOCATIONS.DOMAIN.CREATED`, `DELETED`, `ITEM_ADDED` / `ITEM_REMOVED` (kind, item) and `CLEARANCES_CHANGED`. The kinds are a plugin point (`plugins/domain_plugins.py`, `MapDomainKind`, autodiscovered from every app's `plugins/domain_plugins.py`): a kind names its model, its through table and how it is searched. Every seeded platform has one domain: `ingress_locations` (realistic and full modes, never `none`, GIS or not) makes `regulated_domain` ("Map items a superuser may keep to clearances.") with no clearances and no items, once — a re-run never touches it, so a superuser's edits survive. Tests: `tests_clearances`, `tests_more_clearances`, `tests_domains`, `tests_more_ingress`.

### App: `toto.weather`

**Data model** (`models.py`, migration `0001_initial`):

- `WeatherSettings` — a singleton (accessed via `WeatherSettings.get()`, which `get_or_create`s `pk=1`). Holds only `current_provider` and `forecast_provider`, each a provider slug defaulting to `open_meteo`. The admin enforces the singleton (no add when one exists, no delete).
- `WeatherObservation` — one observed reading. FK `address` → `locations.Address` (`related_name="weather_observations"`), `provider` slug, optional FK `workflow_run` → `workflows.WorkflowRun` (`SET_NULL`), `observed_at`, auto `loaded_at`, and the measurement fields `temperature` (°C), `precipitation_mm`, `precipitation_type` (choices: rain/drizzle/snow/sleet/hail), `cloud_cover` (%), `wind_speed_kmh`, `wind_direction_deg`, `visibility_km`. Ordered newest-loaded first.
- `ForecastSession` — a batch produced by one forecast fetch. `provider`, optional `workflow_run` FK, auto `loaded_at`, `valid_from`/`valid_to`, and `resolution_hours` (default 1). Note there is no address FK on the session; locations live on its points.
- `ForecastPoint` — one hour at one location within a session. FK `session` (`related_name="points"`, cascade), FK `address` → `locations.Address`, `valid_at`, and the same measurement fields as `WeatherObservation`. Indexed on `(session, address, valid_at)` and ordered by `valid_at`.

**Providers** (`providers/`): a small pluggable registry. `BaseWeatherProvider` defines `fetch_current(addresses) -> list[dict]` and `fetch_forecast(addresses, start_at, end_at) -> dict`, returning plain dicts keyed by `address_id`. `get_provider(slug)` resolves a class from `_REGISTRY` (raising `ValueError` on unknown slugs); `available_providers()` lists them. The only implementation is `OpenMeteoProvider` (slug `open_meteo`), which calls the keyless Open-Meteo forecast API per address using the address geometry's lat/lng, requests km/h wind and UTC time, and maps WMO weather codes to a precipitation type. Visibility is converted from metres to km. Forecast requests derive `forecast_hours` from the requested window and filter returned hours to `[start_at, end_at]`.

**Workflow integration** (`workflows.py`, `predefined_tasks.py`): fetching runs inside the toto workflow engine rather than in the request cycle.
- Two lambda-backed workflows are defined by slug: `weather-current` (`WEATHER_CURRENT_SLUG`) and `weather-forecast` (`WEATHER_FORECAST_SLUG`). Their bodies are Python source strings (`WEATHER_CURRENT_LAMBDA`, `WEATHER_FORECAST_LAMBDA`) executed by the engine; they resolve the provider, call `fetch_current` / `fetch_forecast`, and persist `WeatherObservation` rows or a `ForecastSession` + bulk-created `ForecastPoint`s. They parse timestamps with `dateutil` when available, falling back to `timezone.now()`.
- A registered predefined task `weather_export_layers` (`WEATHER_EXPORT_SLUG = "weather-export-layers"`) builds GeoJSON from the temperature/precipitation layer providers and writes a `vault.VaultFile` (optionally encrypted), returning `vault_file_id` and a `download_url`.

**HTTP surface** (`urls.py`, `views.py`, `app_name="weather"`): all views require login. `weather_index` renders the map page (`templates/weather/index.html`), passing geocoded addresses as JSON plus Vault buckets/directories for the export UI and the display names of the configured providers. The JSON API triggers workflows and polls them:
- `POST api/refresh/` → triggers `weather-current` via `toto.workflows.api.trigger_workflow`, returns `run_id`.
- `POST api/forecast/load/` → triggers `weather-forecast`.
- `POST api/export/` → triggers `weather-export-layers`.
- `GET api/current/`, `GET api/forecast/` → read back the latest observations / most recent forecast session for requested `location_ids`.
- `GET api/run/<run_id>/status/` → polls a `WorkflowRun`, surfacing the failed node's error and any export `download_url`. All trigger endpoints require a live Celery worker (`celery_available()`, HTTP 503 otherwise) and return a helpful error if the workflows have not been seeded.

**Background task** (`tasks.py`): `auto_refresh_weather` is a Celery `shared_task` (`toto.weather.tasks.auto_refresh_weather`) intended to be driven by a Celery beat schedule. It fetches current conditions for every `Address` with a geometry and stores observations, retrying with backoff on failure. It reads `WeatherSettings`, defaulting to the `open_meteo` provider.

**Map / tactical plugins** (`plugins/`): registered lazily in `WeatherConfig.ready()`.
- `location_layer_plugins.py` exposes `weather_temperature_layer()` and `weather_precipitation_layer()`, each returning a map-overlay layer dict of the latest per-address observation, buffering point geometries into ~25 km circles so they render as filled polygons.
- `field_plugins.py` provides `weather_map_features()` (GeoJSON features of latest readings) and `weather_metrics_section()` (a KPI panel). These are registered against `toto.tactical`'s `FieldMapPlugin` / `FieldMetricsPlugin` **only if `toto.tactical` is installed** — the app degrades gracefully when it is not. `ready()` also imports `predefined_tasks` to register the export task.

**Management commands** (`management/commands/`):
- `seed_weather_workflows` — idempotently creates/updates the current, forecast and export workflows and their lambda functions in the workflow engine. Must be run before the trigger endpoints will work.
- `ingress_weather` — an `IngressCommand` that seeds the default `WeatherSettings` singleton and invokes `seed_weather_workflows`.

**Admin** (`admin.py`): read-only admins for observations and forecast sessions (with an inline of forecast points), plus the singleton settings admin.

### Key couplings and design notes

- **Locations are the anchor.** Everything keys off `locations.Address.geometry`; only geocoded addresses participate.
- **Runtime coupling to other apps.** The code imports `toto.locations` (its package-mate), `toto.workflows` (engine, `WorkflowRun`, `trigger_workflow`; `toto-flow`), `toto.vault`, `toto.ui.PageProcessor` (guarded import), `toto.celery_utils`, `toto.ingress`, and optionally `toto.tactical`. Guarded/optional imports keep the app functional when peers such as the UI decorator or tactical app are absent.
- **Provider abstraction.** New weather sources are added by subclassing `BaseWeatherProvider` and registering the class in the providers registry; the rest of the app is provider-agnostic and only stores/reads provider slugs.
- **Fetching is asynchronous.** Observations and forecasts are produced by Celery-backed workflow runs (or the beat task), not synchronously in views, so the UI triggers a run and polls its status.

## Usage

`toto-geo` is not used standalone; it is installed into a toto host project as one of the suite's wheels and its apps are enabled as Django apps. The steps below are the weather app's; the map itself is `toto.registry.LOCATIONS_APPS` in `INSTALLED_APPS`, `HAS_GIS` in the settings, `toto.locations.urls` under a `locations/` prefix (namespace `locations`), and `python manage.py ingress_locations` to seed it.

Install (in a host that pins the toto suite):

```bash
pip install -r requirements.toto.txt   # includes toto-geo alongside its siblings
```

Enable the app and run its migrations (the app label is `weather`; the config is `toto.weather`):

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "toto.weather",
]
```

```bash
python manage.py migrate weather
```

Seed configuration and workflows (required before the API endpoints will trigger runs):

```bash
python manage.py seed_weather_workflows   # creates current/forecast/export workflows
python manage.py ingress_weather          # seeds WeatherSettings + workflows
```

Runtime prerequisites: a running Celery worker (the trigger endpoints return HTTP 503 without one), and — for scheduled refreshes — a Celery beat entry driving `toto.weather.tasks.auto_refresh_weather`. Wire the app's URLs into the host under a `weather/` prefix.

Triggering a run programmatically:

```python
from toto.workflows.api import trigger_workflow
from toto.weather.workflows import WEATHER_CURRENT_SLUG

run = trigger_workflow(WEATHER_CURRENT_SLUG, {
    "location_ids": [1, 2, 3],
    "provider": "open_meteo",
})
```

Developing against the source tree: the package lives at `src/toto/weather/` and is an editable member of the mono-repo. Add a provider by implementing `BaseWeatherProvider` and registering it in `providers/__init__.py::_REGISTRY`.

## Build & packaging

`toto-geo` is part of the lockstep-versioned **toto** suite: all sixteen distributions share one `VERSION` (currently **2.0**) and their cross-dependencies are pinned exactly to that version. This wheel declares sibling pins on **`toto-base`** and **`toto-flow`** (`==2.0`); the runtime peers of its apps outside the wheel (`toto.workflows`, `toto.vault`, `toto.people`, etc.) come from those two siblings and are guaranteed to match because a host installs every toto package at one version. `toto-works` and `toto-business` pin this wheel since 2026-10-04, because `toto.kanban` and `toto.company` have foreign keys into the map's models. It is the only wheel of the suite that may need GDAL (`HAS_GIS` on); a host that does not pin it needs none.

Packaging is standard setuptools (`setuptools>=69`, `build_meta`) discovering packages under `src/` and including template/static/graph package-data. Versions are rewritten only by the repo's release tooling (`scripts/release.py`) — never edit them by hand — and a package-graph check keeps each wheel's slice of `toto.*` disjoint. Hosts pin this wheel in `requirements.toto.txt`. For the full build, versioning and release manual, see the repository root README.
