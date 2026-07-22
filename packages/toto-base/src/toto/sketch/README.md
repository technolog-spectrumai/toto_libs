# toto.sketch

*(Studio only — requires BUILD_STUDIO=1)*

Collaborative real-time whiteboard over WebSockets. Users draw on a shared canvas; object state is broadcast via Django Channels.

## Models

- `Board` — a named whiteboard. Fields: `name`, `slug`, `community` (FK, nullable), `created_by` (FK to `people.Person`), `is_public`, `created_at`.

- `BoardObject` — a persistent canvas object. Fields: `board` FK, `object_id` (UUID, stable client-side ID), `object_type` (`shape / text / image / connector / sticky`), `data` (JSON — position, size, color, content), `z_index`, `created_by` (FK to `people.Person`), `updated_at`.

## WebSocket protocol

- Connect to `ws://.../ws/sketch/{board_slug}/`
- Channels consumer handles `object_create`, `object_update`, `object_delete` messages and broadcasts to all connected users.
- `BoardObject` records are persisted on each mutation so boards survive reconnects.

## Key coupling

- Requires Django Channels + Redis channel layer (studio mode).

## Dependencies

- `people` — Board creator and participant Person FKs
- `vault` — Board snapshots stored as VaultFile
