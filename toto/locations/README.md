# toto.locations

GIS-backed geographic model layer. All spatial data uses PostGIS SRID 4326 (WGS84). Models extend `DomainEntity`.

## Models

- `Address` — a postal + geographic point. Fields: `street`, `city`, `postal_code`, `country`, `point` (PostGIS `PointField`, nullable), `community` (FK to `socialhub.Community`).

- `Territory` — a large geographic area (country, region). Fields: `name`, `code`, `polygon` (PostGIS `MultiPolygonField`, nullable).

- `Zone` — a named sub-area within a community or territory. Fields: `name`, `community` FK, `territory` FK, `polygon` (PostGIS `PolygonField`, nullable), `zone_type` (e.g. `residential`, `commercial`, `industrial`, `emergency`).

- `RouteChain` — an ordered sequence of `Route` objects forming a multi-segment path. Fields: `name`, `routes` (M2M to `Route`).

- `Route` — a named spatial path. Fields: `name`, `start_address` / `end_address` (FKs to `Address`), `linestring` (PostGIS `LineStringField`, nullable), `route_type` (`road / rail / water / air / other`), `distance_km`, `estimated_duration_minutes`, `is_active`.

- `MapLayer` — a named data layer for map display. Fields: `name`, `layer_type` (`geojson / wms / tile`), `source_url`, `style_config` (JSON), `is_public`, `community` FK.

- `MapLayerPolygon` — a polygon feature within a map layer. Fields: `layer` FK, `name`, `polygon` (PostGIS `PolygonField`), `properties` (JSON).

## Key coupling

- `detections.Detection` — geographic anchors (`address`, `zone`, `route`)
- `mobilization.EmergencyStatus.zone` — emergency zones
- `response.EvacuationRoute`, `DeploymentRoute` — routes used in field operations
- `inventory.InventorySite.address` — site locations
- `people.Person.address` — person home address
- `events.ScheduledEvent.address` — event venue
- `travels.Visit.location` — visit destinations
