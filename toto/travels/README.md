# toto.travels

Travel & visit log — route journeys and place-visit reviews, surfaced as **Travels**
and **Visits** sub-tabs of the Locations app.

## Gating

Opt-in, off by default. Enable with `BUILD_TRAVELS=1` (or `manage.py … --travels`).
Its only dependencies are `locations` and `people`, both always installed — no tier
required. When the flag is off the app is not installed, its URLs are not mounted, and
the Locations tab bar hides the Travels/Visits tabs (`{% if "toto.travels"|app_installed %}`).

## Models

**`Travel`** — a group journey along a route.
- `participants` — M2M → `people.Person` (related_name `travels`).
- `route` — FK → `locations.Route`, `SET_NULL`, nullable (related_name `travels`).
- `info` — free-text notes about the travel.
- `score` — 1–5, nullable (enforced by the `travel_score_between_1_and_5` check constraint).
- `reviewed_at`, `starts_at` (required), `ends_at` (required) — datetimes.
- Properties: `duration`, `duration_display` (e.g. "2d 3h").

**`Visit`** — one person visiting one address.
- `participant` — FK → `people.Person`, `CASCADE` (related_name `visits`).
- `location` — FK → `locations.Address`, `SET_NULL`, nullable (related_name `visits`).
- `visited_at`, `ends_at`, `reviewed_at` — nullable datetimes.
- `score` — 1–5, nullable (`visit_score_between_1_and_5` check constraint).
- `review` — optional review text.
- Properties: `length_of_stay`, `length_of_stay_display`.

## Integration with Locations

The app is a satellite of `locations`; its templates extend `locations/base.html` and it
registers into the Locations plugin registries via `apps.py::ready()`
(`toto.core.plugin_autodiscover`):

- `plugins/map_plugins.py` → `LocationMapPlugin`: Travels drawn from `route.geometry`,
  Visits from `location.geometry`, as overlay features on the Locations map (with notes in popups).
- `plugins/url_plugins.py` → `LocationUrlPlugin`: deep-link resolvers (`travel_review`,
  `visit_review`, `travel_create`, `visit_create`, `address_visit_review`).
- `plugins/context_plugins.py` → `LocationContextPlugin`: injects `map_recent_travels` /
  `map_recent_visits` into the map's "Travels & Visits" overlay panel.
- `plugins/sidebar_plugins.py` — no-op stub (travels surface via the map overlay, not the sidebar).

### Address / Route notes

`locations.Address.note` and `locations.Route.notes` are free-text fields edited on the
Locations detail pages. They are shown read-only on the visit-review (location note) and
travel-review (route notes) pages and in Address/Route/Visit/Travel map popups.

## URLs (`app_name = "travels"`, mounted at `/travels/`)

- `""` → `my_travels` (Travels tab: travel list; links to Metrics and Add travel)
- `visits/` → `my_visits` (Visits tab: visit list; Add visit)
- `metrics/` → `travel_metrics`
- `travels/<pk>/review/`, `travels/new/`, `travels/<pk>/info/`, `travels/<pk>/delete/`
- `visits/new/`, `visits/<address_id>/review/`, `visits/<address_id>/review/submit/`, `visits/<pk>/delete/`

## Demo data

`manage.py ingress_travels --full` seeds ~18 travels and ~21 visits across several named
routes. It looks up existing `people.Person`, `locations.Route`, and `locations.Address`
rows, so seed `socialhub` and `locations` first (the `INGRESS_ALLOWED_APPS` ordering does
this automatically under `ingress_all`).
