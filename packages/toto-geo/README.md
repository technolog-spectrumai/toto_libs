# toto-geo

Everything with geography in it, for the **toto** platform. This distribution ships three apps. **geography** (2026-10-06) is the small one: a person's point, a community's headquarters and zone, a place-name search and a route search, with two models that hold a geometry and no stored route; it is what zenobia installs. **locations** is the map: addresses as points, territories, zones, routes, map layers, map domains, and the links from people, events and communities to them; it moved here from `toto-base` on 2026-10-04 with every geometry-bearing piece, so `toto-base` needs no GIS library. **weather** fetches current conditions and hourly forecasts for the map's geocoded addresses, stores them as observations and forecast sessions, and surfaces them on the map. It is one of sixteen lockstep-versioned wheels that share the `toto.*` PEP 420 namespace and is versioned and pinned together with its siblings. zenobia, the main host, pins this wheel for `toto.geography` alone: it installs neither `toto.locations` nor `toto.weather`.

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

`toto-geo` contains three Django apps under the shared namespace: `toto.geography`, `toto.locations` and `toto.weather` (`AppConfig` name `toto.weather`, verbose name "Weather").

### App: `toto.geography`

Geography with two stored types (2026-10-06). `AppConfig` name `toto.geography`, label and URL namespace `geography`. A host that wants it pins `toto-geo`, adds `toto.registry.GEOGRAPHY_APPS` after `CORE_APPS`, installs `django.contrib.gis`, runs a spatial database (PostGIS, or SpatiaLite in development and the tests) and mounts `toto.geography.urls`. It needs nothing of `toto.locations`: the two apps share no table, no URL, no template and no static path, and a host may install either, or both (`tests_models` boots them together).

**Models with a geometry, and only these** (`models.py`, migration `0001_initial`): `Address` (`point`, a `PointField` in SRID 4326 that is never null; `name`, `postal_address`, `note`, `created_by`, two stamps) and `Zone` (`name`, `description`, `outline`, a `PolygonField` of one closed ring, valid and simple, at most 500 corners; `created_by`, two stamps). No line is stored anywhere: no `Route`, `RouteChain`, `Territory`, `MapLayer` or `MapDomain`, no latitude or longitude as numbers, and no JSON field but the usage event's `metadata`. A geography `Zone` is an area with a name; it has no type and belongs to no territory, unlike the map's.

**Plain link rows.** `PersonAddress` (person one-to-one, `related_name="geography_address"`; address one-to-one) and `CommunityHeadquarters` (community one-to-one, `related_name="geography_headquarters"`; an address and a zone, each one-to-one and optional). One link per geometry row, always: clearing, removing and erasing delete the `Address` or `Zone` row itself (`saves.py`), and a `post_delete` receiver on each link row (`receivers.py`) does the same when the person or the community goes. toto-base keeps no geometry: `Person.address` and `Community.seat` stay the one postal text of each, and nothing here writes them.

**Who sees a person's point** is `access.visible_point(viewer, person)`, the one function behind the profile page and the doors: `Person.show_address`, read through `socialhub.contact_access.may_see_address`, governs the point exactly as it governs the address text (the owner and an administrator always see it). The Django admin registers `GeographyUsageEvent` (read only) and `GeographyQuotaPolicy`; `Address`, `Zone` and the link rows are not registered, so plain staff has no list that goes around the switch and no admin page draws GeoDjango's map widget. A community's headquarters and zone are set by its head or an administrator (`socialhub.permissions.may_moderate_community`) and seen by every signed-in member.

**Doors** (`urls.py`, `views.py`): JSON, POST only, a session cookie and the CSRF token, refusals as `{"error": …}` with 400, 402, 403, 404, 405, 409, 429 or 503, and `Cache-Control: no-store`. `api/search/` `{q, op}` answers `{results: [{label, lat, lng}], charged}`; `api/route/` `{from: {lat, lng}, to: {lat, lng}, mode, op}` (mode `car`, `bicycle` or `foot`) answers `{line, distance_km, duration_min, charged}`; `me/address/` `{lat, lng, name, note, op}` and `me/address/clear/`; `communities/<slug>/headquarters/` `{lat, lng, name, note, op}` and `communities/<slug>/zone/` `{name, description, outline, op}` (the outline is a list of `[lat, lng]` corners, the ring open), each with `clear/` under it. Each view carries `geography_door`, `"signed-in"` or `"moderator"`, the mark a walk over the URLconf reads.

**The charged-door rule** (`charging.py`) is one for every charged door. `op` is a UUID the browser mints once per deliberate press and repeats on a retry; a charged door without one answers 400. The usage event's key is `<metric>:<user>:<op>` and its `metadata` holds `{"request": <hex>}` and nothing else, a keyed digest (`salted_hmac`) over the user, the op and the normalised body. A known op with the same digest within ten minutes is a replay, answered again and charged nothing; a known op with another digest, or after that time, is 409. A fresh op is checked for funds first (402 before any provider is asked or anything is written); then the work; then, in one transaction, the rows of a save, the usage event and the charge. A ledger refusal after the work rolls all of it back and the answer is withheld. The event is written in its own savepoint and a concurrent duplicate is caught outside it, because `quota.record_usage` swallows the uniqueness error inside the caller's `atomic()` and Postgres then refuses the rest of the block. A provider failure is 503; an empty answer, "no route" and a save that changes nothing are answered uncharged.

**Five metrics** (`metrics.py`; the prices are seeds in `toto.mana.colours`): `geography.lookup` (0.5) and `geography.route` (1) draw on compute mana; `geography.pin` (0.5, a saved point), `geography.zone` (1) and `geography.note` (0.2, a later change to a saved point or zone) draw on storage mana. Removing is free. `entitlements.py` declares `geography` free on every plan.

**Place-name search** (`places.py`) asks the host's geocoder through `geocode.py`: 20 a minute per member, one provider call a second shared by everybody, answers kept `GEOGRAPHY_GEOCODE_CACHE_SECONDS` (3600; 0 turns the cache off) under keyed-hash keys that name neither the query nor a member. A new search answered from the cache is still charged; a replay of the same op is not. **`geocode.py` exists twice in this wheel, on purpose**: `toto/geography/geocode.py` is a byte-for-byte copy of `toto/locations/geocode.py` (the Nominatim adapter on urllib, reading `LOCATIONS_GEOCODING`), so geography imports nothing of the parked map and the original stays as it is, with its tests and its logger name. `tests_places` fails when the two differ.

**Route search** (`routing.py`) asks an OSRM endpoint per mode from `GEOGRAPHY_ROUTING` (`{"enabled", "endpoints": {car, bicycle, foot}, "timeout": 10}`; FOSSGIS's public router by default; no public transport): 10 a minute per member and the same one-a-second throttle. Both ends are `{lat, lng}` and nothing else; a row id or a name in an end is a 400, so the door cannot be asked where a stored point is. The line is answered and written to no model, no cache, no file, no log and no audit record. A replay calculates again and charges nothing more.

**The map widget** (`templates/geography/_map.html`, `static/geography/map.js`, `zone_draw.js`, `pin.js`, `mapview.py`) is Leaflet with OpenStreetMap's tiles (`_tiles.html`, the tile helper of the map under this app's own name; `pin.js` is a copy of the map's `oya/pin.js`). It is included by two plugins, a `ProfilePlugin` on the Overview (key `geography_address`) and a `CommunityPlugin` (key `geography_headquarters`), and Leaflet's tags are drawn only where one of them draws a map. The search runs on Enter or the button, never while typing; a click places a temporary point that lives in the page's memory only; the route panel sends the two coordinate pairs the page already holds; `zone_draw.js` is a hand-written outline editor with no plugin. The host serves Leaflet 1.9.4 at `static/vendor/leaflet/`.

**The audit chain** (`audit.py`): `GEOGRAPHY.ADDRESS.SAVED` and `.CLEARED`, `GEOGRAPHY.HEADQUARTERS.SAVED` and `.CLEARED`, `GEOGRAPHY.ZONE.SAVED` and `.CLEARED`, `GEOGRAPHY.SEARCH`, `GEOGRAPHY.ROUTE`. Each holds the actor, the community if any, the metric, the amount and the outcome; a save also holds its link row's id; never the query, a coordinate, a line, a distance or a duration. **Erasure and export** (`erasure.py`, `plugins/personal_data_plugins.py`): `toto.core.erasure` calls this app where it is installed, so an erased member's point is deleted, and "Download my data" holds the member's point, its name and its note.

Tests: `tests_models`, `tests_places`, `tests_routes`, `tests_map_js` (the two scripts run in node), `tests_map_tiles`, `tests_profile`, `tests_headquarters`, `tests_audit`.

### App: `toto.locations`

The map. It was an app of `toto-base` until 2026-10-04 and moved here with every geometry-bearing piece (this section moved with it, from that package's README); it kept its import path, app label (`locations`), URL namespace and migrations. A host that wants it pins `toto-geo` and adds `toto.registry.LOCATIONS_APPS` after `CORE_APPS` — it is never part of `CORE_APPS` or `BASE_APPS`.

GIS models (SRID 4326) extending `DomainEntity`: `Address` (postal fields, a latitude and a longitude as floats, and a nullable `PointField` that mirrors them), `Territory` (a named area, `MultiPolygonField`, with an optional capital address), `Zone` (a typed sub-area of one territory: a `PolygonField`, a zone type, and a required key to its `Territory`), `Route` / `RouteChain` (typed `LineStringField` paths), and `MapLayer` / `MapLayerPolygon` (thematic overlays). Enables spatial queries used across the suite (detections in a zone, nearest inventory site, etc.). Ships a small JSON API under `/locations/api/` for zones, addresses, map data, layers and route search.

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
