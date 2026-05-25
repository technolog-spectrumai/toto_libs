# toto.enigma

*(Studio only — requires BUILD_STUDIO=1)*

Real-time chat over WebSockets. Rooms hold participants; messages are delivered via Django Channels consumers backed by a Redis channel layer.

## Purpose

Community members connect to `ws://.../ws/enigma/{room_slug}/` and exchange messages in real time. Messages are ephemeral — they are relayed through the Redis channel layer but not persisted to the database. `Room` records define the chat spaces; `Participant` records gate who can access a room. Rooms can be community-scoped (for community channels) or platform-wide (for cross-community coordination).

## Models

- `Room` — a chat space. Fields: `name`, `slug` (unique), `community` (FK to `socialhub.Community`, nullable — can be community-scoped or platform-wide), `is_private`, `created_by` (FK to `people.Person`), `created_at`.

- `Participant` — a person's membership in a room. Fields: `room` FK, `person` (FK to `people.Person`), `joined_at`, `last_read_at`, `is_admin`. Unique on `(room, person)`.

Messages are not persisted to the database — they are relayed ephemerally through the Redis channel layer. If message history is needed it must be added separately.

## WebSocket protocol

- Connect to `ws://.../ws/enigma/{room_slug}/`
- The Channels consumer authenticates the session and places the socket into a Redis channel group named `enigma_{room_slug}`.
- Incoming messages are broadcast to all members of the group.

## Key coupling

- `channels_redis.core.RedisChannelLayer` — required at runtime (studio mode only).
- `socialhub.Community` — optional community scoping for rooms.
