# Forum — Real-time Chat

Discord-style chat: persistent channels, permanent readable history, full-text search, over
Django Channels WebSockets plus a JSON API.

Transport security is **TLS and nothing else**. Messages are stored as plaintext rows, which
is what makes durable, paginated, searchable history possible — a member who joins today can
read everything said before they arrived, and the server can run a text query over it.

> Renamed from **telegraph** and stripped of three cryptographic schemes (gervazy at-rest
> message encryption, client-side "secure-on-send" E2E, and an MLS relay over a prebuilt
> rotor WASM bundle) plus a 24-hour message TTL. Those existed to support a Signal-style
> privacy story; they cost a deployment secret whose loss made history unrecoverable, capped
> history at one day, and made message bodies impossible to query.

## Data model ([models.py](models.py))

- `ForumChannel` — name, slug, creator, and its members.
- `ForumMember` — the **only** membership record. A parallel `participants` M2M used to exist
  alongside it; the two disagreed, and a user dropped from one but not the other could still
  post over a raw websocket. See [permissions.py](permissions.py).
- `ForumMessage` — a plaintext message: `body`, optional `attachment` (a real file on disk,
  not a base64 `data:` URL), `reply_to`, `edited_at`, `deleted_at` (soft delete, so replies
  keep their anchor). Never expires — retention is deferred work, see
  [forum_todo.md](forum_todo.md).

**Attachments are not under `MEDIA_ROOT`.** nginx serves `/media/` unauthenticated with a
30-day cache, which would leave a private channel's images readable forever by anyone who
ever saw the URL — including a member who has since left. They live under
`settings.FORUM_ATTACHMENT_ROOT` (default: a `forum_attachments/` sibling of `MEDIA_ROOT`)
and are handed out only by `MessageAttachmentApiView`, which applies the same membership
check as the message they belong to.

## Permissions ([permissions.py](permissions.py))

Every entry point routes through one module:

| Who | May |
|-----|-----|
| anonymous | nothing — not even the channel list |
| signed in | browse channels, create one, join one |
| active member | read history, search, post, edit/delete their own messages |

Rosters, message history, search results and attachments are all scoped to active
membership. That scoping is not optional: messages are permanent now, so a leak here is
durable.

Membership is re-checked on live sockets too. A member removed via the REST/HTML leave
endpoints or the Django admin does not have their tab closed by any of those paths, so
`signals.py` broadcasts a control frame on every `ForumMember` write and the consumer
re-checks and disconnects. (Those endpoints deactivate per instance rather than with a
queryset `.update()`, because only instance saves fire the signal.)

## Search ([search.py](search.py))

Dual-backend, chosen at query time by `connection.vendor`:

- **postgresql** — `SearchVector`/`SearchQuery`/`SearchRank`, computed per query.
- **anything else** — case-insensitive substring matching.

Deliberately no `SearchVectorField` and no `GinIndex` in the migration: every host runs
**SpatiaLite in dev and in every clean-env gate, PostGIS in deployment**, and Postgres-only
DDL in `Meta.indexes` would break `manage.py migrate` on the SQLite side. The UI surfaces
which engine actually ran. Promoting to a stored, indexed vector is tracked in
[forum_todo.md](forum_todo.md).

## History ([store.py](store.py))

A freshly-connected socket receives the newest 50 messages. Older pages are fetched on
demand through
`GET /forum/api/channels/<slug>/messages/?before=<iso8601>&before_id=<uuid>&limit=`.

The cursor is the **`(created_at, id)` pair** of the oldest message you already hold, not
the timestamp alone: `created_at` is not a total order, so a timestamp-only cursor drops a
message whenever two share a timestamp across a page boundary, and the sort itself is
unstable.

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET · POST | `/forum/api/channels/` | List channels · create one |
| GET | `/forum/api/channels/{slug}/` | Channel detail + members (roster is members-only) |
| GET | `/forum/api/channels/{slug}/messages/` | Paginated history (`?before=&limit=`) |
| POST | `/forum/api/channels/{slug}/join/` · `/leave/` · `/leave-all/` | Membership |
| POST | `/forum/api/channels/{slug}/upload/` · `/upload-audio/` | Image / voice attachment |
| GET | `/forum/api/search/` | Message search (`?q=&channel=`) |
| GET | `/forum/api/messages/{uuid}/attachment/` | Membership-checked attachment download |

Auth and identity endpoints (`login`, `logout`, `me`, `me/mesh`, `health`, `apps`) are **not
here** — they were never chat. They live in [toto.api](../../../../toto-base/src/toto/api/)
and are mounted at `/api/`, with a legacy `/telegraph/api/` alias for a shipped enigma
desktop binary that cannot be updated in lockstep with the server.

## WebSocket Message Types

`ws/forum/<channel_slug>/`

| Type | Direction | Description |
|------|-----------|-------------|
| `chat_message` | both | A message (optionally with `reply_to`) |
| `image_message` / `voice_message` | server→client | Broadcast of an uploaded attachment |
| `chat_history` | server→client | The newest page, replayed on connect, plus `has_more` |
| `message_edit` / `message_delete` | both | Author-only mutation of an existing message |
| `typing_start` / `typing_stop` | both | Presence only, never persisted, never echoed to the sender |
| `room_participants` | server→client | Roster plus `online` — who actually has a socket open ([presence.py](presence.py)), which drives the presence dots |
| `membership_changed` | internal | Control frame from [signals.py](signals.py); makes a consumer re-check membership and disconnect if revoked |
| `system_error` | server→client | Error notification |

## Operations

Nothing to provision — no vault, no secret, no build artifact. The app needs a Redis channel
layer (for websockets and for the membership-revocation control frame), a cache (presence),
and a writable `FORUM_ATTACHMENT_ROOT`.

**Messages are never deleted.** There is no purge job at all; see
[forum_todo.md](forum_todo.md) for the retention work that replaces the old TTL.

## Testing

```bash
cd zenobia && .venv_test/bin/python manage.py test \
  toto.forum.tests.test_api_views toto.forum.tests.test_consumers \
  toto.forum.tests.test_history toto.forum.tests.test_models toto.forum.tests.test_views
```

Name the test modules explicitly: `toto` is a PEP 420 namespace package, so
`manage.py test toto.forum` cannot be discovered by unittest.
