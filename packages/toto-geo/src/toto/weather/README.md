# toto.weather

*(Studio only — requires BUILD_STUDIO=1)*

Weather data ingestion and forecasting. Stores observations and forecast sessions, typically populated by workflow nodes that call external weather APIs.

## Models

- `WeatherSettings` — platform-wide weather configuration. Fields: `provider` (slug of weather API), `api_endpoint`, `units` (`metric / imperial`), `update_interval_minutes`, `is_active`, `community` (FK, nullable).

- `WeatherObservation` — a single observed data point. Fields: `address` (FK to `locations.Address`), `observed_at`, `temperature_c`, `humidity_pct`, `wind_speed_kmh`, `wind_direction_deg`, `precipitation_mm`, `condition` (slug), `raw_data` (JSON), `workflow_run` (FK to `workflows.WorkflowRun`, nullable).

- `ForecastSession` — a batch of forecast points generated in one API call. Fields: `address` (FK), `generated_at`, `provider`, `workflow_run` FK, `raw_response` (JSON).

- `ForecastPoint` — one time-step within a forecast. Fields: `session` FK, `forecast_at`, `address` FK, `temperature_c`, `humidity_pct`, `wind_speed_kmh`, `precipitation_mm`, `condition`.

## Key coupling

- `locations.Address` — observations and forecasts are anchored to addresses.
- `workflows.WorkflowRun` — weather fetch is a workflow node type; each run links its observation/forecast to the triggering workflow run.

## Dependencies

- `locations` — WeatherObservation.location FK to Address
- `workflows` — ForecastSession triggered by workflow node; WorkflowRun FK
