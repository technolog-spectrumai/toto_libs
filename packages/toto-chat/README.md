# toto-chat

`toto-chat` is the forum chat distribution of the **toto** suite: Discord-style group chat organised into named, persistent channels with permanent, paginated, searchable history. It ships a single Django app, `toto.forum`, that combines a real-time Django Channels WebSocket transport, a JSON API, and server-rendered HTML views. Confidentiality is TLS in transit only — messages are stored as plaintext rows, which is precisely what makes durable history, full-text search, and "read everything said before you joined" possible. It is one of 9 lockstep-versioned wheels sharing the `toto.*` PEP 420 namespace and depends on `toto-base`.

## What it does (functional)

toto-chat gives an operator a self-hosted, real-time team chat that behaves like Discord or Slack, with the deliberate twist that **history is permanent and fully searchable**.

For an end user who is signed in:

- **Channels** — browse the list of channels, create a new one, and join or leave channels. Joining a channel gives you access to its entire past, not just messages sent after you arrived.
- **Messaging** — post text messages, reply to a specific earlier message (threading anchor), and edit or delete your own messages. Deletes are soft: a removed message leaves a placeholder so replies to it still make sense and the conversation keeps its shape.
- **Attachments** — upload image and voice (audio) attachments. These are stored as real files, so large uploads do not bloat the message history.
- **Live experience** — messages appear in real time; typing indicators show when someone is composing; presence dots show who actually has the channel open right now; a live roster lists channel members.
- **Search** — run a text query across the channels you belong to and jump to matching messages. The UI tells you which search engine actually served the query.
- **Permanent, paginated history** — a freshly opened channel shows the most recent messages and lets you scroll back through everything ever said, page by page.

Privacy and access, in plain terms:

- Anonymous visitors see **nothing** — not even the channel list.
- Signed-in users can browse and join channels.
- Only **active members** of a channel can read its history, search it, post to it, and download its attachments.
- Private-channel content and attachments are scoped to current membership, and that scoping is enforced live: if someone is removed from a channel (via the app or the Django admin), any tab they still have open is disconnected on the spot.

There is nothing to provision beyond infrastructure: no vault, no encryption secret, no build artifact to generate. The trade-off is explicit — because rows are plaintext, they are readable at rest, and messages currently never expire.

## How it works (technical)

toto-chat contains exactly one app/module: **`toto.forum`** (app label `forum`), installed under the shared `toto.*` namespace. The sections below fold that app's design into one place.

### forum

**Surfaces.** The app exposes three coordinated entry points, all routed through a single permissions module:

- **HTML views** (`views.py`, templates under `templates/forum/`) — channel list, channel create, channel detail, join/leave, and a search page.
- **JSON API** (`api_views.py`, mounted under the app's `/api/` prefix).
- **WebSocket transport** (`consumers.ChatConsumer`, an `AsyncWebsocketConsumer`) at `ws/forum/<channel_slug>/`, wired through `routing.websocket_urlpatterns`. Each channel maps to a channel-layer group named `forum_<slug>`. There is no MLS relay, no client-side encryption, and no CRDT mirror — the database is the single source of truth for the message list.

**Data model (`models.py`).**

- `ForumChannel` — `name`, `slug`, `created_by` (→ `AUTH_USER_MODEL`), and a `people` M2M to `people.Person` **through** `ForumMember`. `created_at`; ordered by name.
- `ForumMember` — the **only** membership record (a parallel `participants` M2M was removed because the two could disagree and let a dropped user still post over a raw socket). Links a `ForumChannel` to a `people.Person`, with `joined_at` and an `is_active` flag. A unique constraint prevents duplicate `(channel, person)` rows and a check constraint requires a person — membership is always a human.
- `ForumMessage` — a plaintext message keyed by `UUID`. Fields include `channel`, `sender` (→ `AUTH_USER_MODEL`, `SET_NULL`), denormalised `sender_name`/`sender_avatar_url` (so history renders without re-resolving membership), `msg_type` (`chat_message` / `image_message` / `voice_message`), `body`, a `reply_to` self-FK, `created_at` (indexed), `edited_at`, and `deleted_at` (soft delete). Two composite indexes on `(channel, created_at)` and `(channel, -created_at)` support forward and reverse history scans. Messages are never expired automatically; retention is deferred work.
- **Attachment storage is deliberately not under `MEDIA_ROOT`.** Image/voice payloads are `FileField`s stored via `forum_attachment_storage` at `settings.FORUM_ATTACHMENT_ROOT` (default: a `forum_attachments/` sibling of `MEDIA_ROOT`), under `<channel-slug>/<message-uuid><ext>`. Because nginx serves `/media/` unauthenticated with a long cache, keeping attachments outside that tree is what lets them follow the same membership rule as their message; they are handed out only by `MessageAttachmentApiView`, which applies the message's `can_read` check.

**Permissions (`permissions.py`) — single source of truth.** Every surface routes through `can_browse` / `can_read` / `can_send` / `can_moderate`, plus `readable_channels(user)` (the search scope). `member_for` / `is_member` resolve the caller's `people.Person`, then their active `ForumMember` row. `can_moderate` lets authors edit/delete their own messages and lets staff delete anything. The consumer performs the same `can_send` check at connect time and closes with code `4403` on failure.

**Live membership revocation (`signals.py`, `apps.ForumConfig.ready`).** Leave endpoints and the admin deactivate memberships **per instance** (not via a queryset `.update()`) so that a `post_save` on `ForumMember` fires. `signals.py` broadcasts a `membership_changed` control frame over the channel layer; the connected consumer re-checks membership and disconnects if it has been revoked. `apps.ready()` imports `signals` so this is armed on startup.

**Search (`search.py`) — dual backend, chosen at query time.** `search_messages` scopes to `readable_channels`, excludes deleted and empty bodies, then branches on `connection.vendor`: PostgreSQL uses `SearchVector`/`SearchQuery`/`SearchRank` (`websearch` parse, English config, ranked); any other backend falls back to case-insensitive `body__icontains`. `search_mode()` returns a context dict (`search_engine`, `search_fallback_used`) so the template can surface which engine ran. There is intentionally **no** `SearchVectorField` and **no** `GinIndex` in the migration, because Postgres-only DDL in `Meta.indexes` would break `manage.py migrate` against the SpatiaLite database used in dev and in the clean-env test gates (PostGIS is used in deployment).

**History and pagination (`store.py`).** A newly connected socket receives the newest page (default 50 messages, `DEFAULT_HISTORY_LIMIT`) plus a `has_more` flag. Older pages are fetched on demand from the messages API. The pagination cursor is the **`(created_at, id)` pair** of the oldest message already held — not the timestamp alone — because `created_at` is not a total order and a timestamp-only cursor would drop messages that share a timestamp across a page boundary.

**Presence (`presence.py`).** Backed by the cache; tracks who currently has a socket open per channel. The consumer broadcasts `room_participants` (roster + `online` set) on connect/disconnect to drive presence dots.

**WebSocket message types** (`ws/forum/<channel_slug>/`): `chat_message` (both directions, optional `reply_to`); `image_message` / `voice_message` (server→client broadcasts of uploads); `chat_history` (recent page replayed on connect, with `has_more`); `message_edit` / `message_delete` (author-only mutations); `typing_start` / `typing_stop` (presence only, never persisted, never echoed to sender); `room_participants` (roster + online); `membership_changed` (internal control frame); `system_error`.

**Key couplings and dependencies.**

- Depends on **`toto-base`** (`toto-base==1.6`) for the `people.Person` model (membership identity), the `toto.api` layer, and `toto.ingress` (management-command base).
- Requires a **Redis channel layer** (WebSocket delivery and the membership-revocation control frame) and a **cache** (presence), plus a writable `FORUM_ATTACHMENT_ROOT`.
- Auth/identity endpoints (`login`, `logout`, `me`, `me/mesh`, `health`, `apps`) are intentionally **not** in this package — they are not chat and live in `toto.api`, mounted by the host at `/api/` (with a legacy `/telegraph/api/` alias for a shipped desktop binary).
- `ingress_forum` (management command, extends `toto.ingress.IngressCommand`) seeds sample channels (`CandyLand`, `Announcements`) with the `admin` user on the roster and grants that user data-mesh read access; it is gated on the ingress `--full` flag.

**History note.** The app was renamed from **telegraph** and had three cryptographic schemes (at-rest message encryption, client-side secure-on-send E2E, and an MLS relay over a prebuilt rotor WASM bundle) plus a 24-hour message TTL removed. Those supported a Signal-style privacy story but cost a deployment secret whose loss made history unrecoverable, capped history at one day, and made message bodies impossible to query. The current design keeps TLS in transit and stores plaintext rows to make permanent, searchable, paginated history possible.

### API endpoints

The app is mounted by the host (conventionally at `/forum/`). Relative to that mount:

| Method | URL | Description |
|--------|-----|-------------|
| GET · POST | `api/channels/` | List channels · create one |
| GET | `api/channels/<slug>/` | Channel detail + members (roster is members-only) |
| GET | `api/channels/<slug>/messages/` | Paginated history (`?before=<iso8601>&before_id=<uuid>&limit=`) |
| POST | `api/channels/<slug>/join/` · `leave/` · `api/channels/leave-all/` | Membership |
| POST | `api/channels/<slug>/upload/` · `upload-audio/` | Image / voice attachment |
| GET | `api/search/` | Message search (`?q=&channel=`) |
| GET | `api/messages/<uuid>/attachment/` | Membership-checked attachment download |

HTML routes: `` (channel list), `create/`, `search/`, `<slug>/`, `<slug>/join/`, `<slug>/leave/`.

## Usage

toto-chat is a Django app distributed as a wheel; it is used from inside a toto host project, not run standalone.

**Install.** In practice the version is pinned by the host in `requirements.toto.txt` alongside the other lockstep siblings:

```
toto-chat==1.6
```

For local development against a checkout, install the package (editable) from its directory; it pulls `toto-base==1.6` transitively.

**Wire it into a host.**

1. Add the app to `INSTALLED_APPS`:
   ```python
   INSTALLED_APPS = [
       # ...
       "toto.forum",
   ]
   ```
2. Include its URLs, e.g.:
   ```python
   path("forum/", include("toto.forum.urls")),
   ```
3. Add the WebSocket routes to your ASGI application:
   ```python
   from toto.forum.routing import websocket_urlpatterns
   ```
4. Configure a **Redis channel layer** (`CHANNEL_LAYERS`), a **cache** (for presence), and optionally `FORUM_ATTACHMENT_ROOT` (defaults to a `forum_attachments/` sibling of `MEDIA_ROOT`).
5. Run migrations and serve over ASGI (daphne/uvicorn), since the app needs WebSockets:
   ```bash
   python manage.py migrate
   ```

**Seed sample data (optional):**

```bash
python manage.py ingress_forum --full
```

Requires an existing `admin` user; creates the `CandyLand` and `Announcements` channels with `admin` on the roster.

**Tests.** Name the test modules explicitly — `toto` is a PEP 420 namespace package, so `manage.py test toto.forum` cannot be discovered by unittest:

```bash
python manage.py test \
  toto.forum.tests.test_api_views toto.forum.tests.test_consumers \
  toto.forum.tests.test_history toto.forum.tests.test_models toto.forum.tests.test_views
```

## Build & packaging

toto-chat is one of the 9 lockstep-versioned wheels in the toto suite. All siblings share a single `VERSION` (currently **1.6**) and pin each other exactly; this package depends on **`toto-base==1.6`**. Versions are rewritten only by the repo's release tooling (`scripts/release.py`) — never edit them by hand — and `scripts/check_package_graph.py` enforces that each wheel owns a disjoint slice of the `toto.*` namespace. Packaging is standard setuptools (`src/` layout, namespace packages, with `templates/`, `static/`, and `graph/*.yaml` bundled as package data). Hosts pin the whole set in `requirements.toto.txt`.

For the full build, versioning, and release manual, see the repository root README.
