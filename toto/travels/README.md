# toto.travels

*(Studio only — requires BUILD_STUDIO=1)*

Route journeys and visit history. Records group trips with geographic waypoints and individual visit reviews.

## Models

- `Travel` — extends `DomainEntity`. A named journey. Fields: `participants` (M2M to `people.Person`), `route` (FK to `locations.Route`, nullable), `community` (FK, nullable), `starts_at`, `ends_at`, `description`, `status` (`planned / ongoing / completed / cancelled`).

- `Visit` — extends `DomainEntity`. One person's visit to a location. Fields: `participant` (FK to `people.Person`), `location` (FK to `locations.Address`), `travel` (FK, nullable), `visited_at`, `duration_minutes`, `notes`, `rating` (1–5), `is_public`.

## Key coupling

- `locations.Route` / `locations.Address` — geographic anchors for journeys and visits.
- `people.Person` — participants and visitors.
