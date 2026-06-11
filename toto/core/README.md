# toto.core

Platform configuration and tenant identity. Defines the top-level `Platform` singleton, the `Federation` it belongs to, and the visual/branding system (`Theme`, `ColorMix`, `Font`).

## Purpose

`core` is the first app that boots. The `Platform` singleton defines this deployment's domain, name, and branding. It is injected into every request by `PlatformMiddleware` so templates render the correct theme without per-view DB queries. `Federation` groups communities into a named network. `DomainEntity` is the abstract base inherited by most domain models — it provides `uuid`, `slug`, `name`, `description`, `logo`, `metadata`, `created_at`, `updated_at` without additional tables.

## Models

- `Font` — a named typeface reference (family name + CSS import URL). Used by `Theme`.
- `ColorMix` — a named palette record (primary, secondary, accent, background, surface, text, border hex values). Used by `Theme`.
- `Theme` — visual identity for a platform or community. Links a `ColorMix` and two `Font` objects (body/heading). Has a `dark_mode` flag and a `custom_css` override field.
- `Federation` — a named grouping of platforms. Extends `DomainEntity` (slug, description, logo, metadata). One-to-one with a `Theme`.
- `Platform` — the singleton record representing this deployment. Fields: `name`, `slug`, `federation` (FK to `Federation`), `domain`, `contact_email`, `theme` (FK to `Theme`), `is_active`. One-to-one back-ref from `backup.BackupProfile`.

`DomainEntity` is the abstract base used by almost every domain model in the system. It provides: `uuid` (auto), `slug` (auto from name), `name`, `description`, `logo`, `metadata` (JSON), `created_at`, `updated_at`.

## Template tags

- `graph_export` — `{% load graph_export %}{% export_to_graph_button obj %}` renders a per-object "Export to graph" button on detail pages, linking to ravioli's export **preview** page. It lives in `core` (always installed) so templates can load it even in builds without the Neo4j layer; it renders nothing unless `RAVIOLI_ENABLED` is set and the object's model is graph-mapped (and not in `RAVIOLI_EXPORT_EXCLUDED_APPS`). The graph apps are imported lazily.

## Key coupling

- `core.Platform` is read at boot time by `sso_master.services.get_active_platform()` to resolve the OIDC issuer URL.
- `backup.BackupProfile` has a one-to-one with `Platform`.
- `Theme` is read by every template that renders the platform's branding.

## Dependencies

None — standalone app.
