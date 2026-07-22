# forum — deferred work

Tracking file for the forum app (formerly `telegraph`). Everything here was consciously left
out of the rework that dropped encryption, made messages permanent and added search.

## Decisions taken in the rework (for context)

| # | Decision |
|---|---|
| D1 | Full rename `telegraph` → `forum`: module dir, app label, URL namespace, WS path, template dir, model class names. Fresh `0001_initial`, new `forum_*` tables. **Pre-rename encrypted messages were dropped, not migrated.** The distribution stays `toto-chat`. |
| D2 | Auth/identity views + `TokenAuthMiddleware` moved into `toto.api` at `/api/`; `/telegraph/api/` kept as a thin alias for the shipped enigma desktop binary. `/forum/` is chat-only. |
| D3 | Rotor WASM, the MLS relay and Yjs all deleted. TLS is the transport security; the DB stores plaintext. |
| D4 | Attachments moved from base64 data-URLs to `FileField` on disk. |
| D5 | Pass 1 parity: persistence, pagination, attachments, search, edit, delete, reply, typing, presence, channel creation. |
| D6 | Read access requires login; history, search and rosters are scoped to active membership. |
| D7 | Search uses `SearchVector`/`SearchRank` at query time on Postgres and `icontains` on SQLite, branched on `connection.vendor`. No `SearchVectorField`, no `GinIndex` in migrations. |

## Deferred

### Message retention / cleanup — the headline item
Messages are now permanent. There is no purge job at all.

Implement as a `forum=False` kwarg in `toto/schedules.py`, modelled on the `monit` block there
(`monit-prune` at `crontab(minute="17")`, retention driven by a `MONIT_RETENTION_HOURS`-style
setting). Add a `forum_prune` Celery task alongside it.

**Do not reintroduce a cron framing.** The old `telegraph_purge_expired` docstring and README
claimed "faros has no celery — run this from cron". That was stale: `faros/faros/celery_app.py`
exists and `faros/faros/settings.py` states beat entries are owned by `toto.schedules`. Both
active hosts run celery worker + beat.

### Storage
- Orphan attachment GC under `FORUM_ATTACHMENT_ROOT` — soft-deleting a message hides its
  attachment from the API but does not remove the file from disk.
- Per-channel or global storage quotas.
- Attachments are served by a Django view (`MessageAttachmentApiView`) so membership can be
  checked. That puts image traffic through the app server rather than nginx; if it ever
  matters, swap in an `X-Accel-Redirect` / `X-Sendfile` handoff, keeping the permission
  check in Django and letting nginx stream the bytes from a location marked `internal`.

### i18n (pre-existing, not caused by this rework)
The hosts' `locale/pl/LC_MESSAGES/django.po` catalogs are stale: their source references
still point at `toto/toto/<app>/...`, the pre-split monorepo layout. Nobody has re-run
`makemessages` since the package split, and running it from a host would now **delete**
every toto-app string, because the app source lives in `toto_libs`, outside the host tree.

Fix properly by teaching `makemessages` about the library checkout (or by generating the
catalog in `toto_libs` and merging). Until then the forum's new UI strings — "Load older
messages", "Reply", "Edit", "Delete", "is typing…", "Search messages", "(edited)",
"This message was deleted." — have no Polish translation and fall back to English.

### Search
- Stored `SearchVectorField` + `GinIndex`, populated by trigger or `RunPython`, behind a
  `connection.vendor == "postgresql"` guard. This is the D7 upgrade path. It was deferred
  because Postgres-only DDL in `Meta.indexes` breaks `manage.py migrate` on the SpatiaLite
  database used by dev and by every `clean_env_test.sh` gate.
- Search filters: by author, by date range, by channel, has-attachment.

### Discord features not in pass 1
- Reactions (new model + WS type pair).
- Unread / read markers, per-user last-read position.
- @mentions and the notification pipeline behind them.
- Pinned messages.
- Roles, permissions and private channels (today: login required, membership gates read/send).
- Channel categories.
- Threads.
- Direct messages / 1:1 conversations.
- Markdown rendering and syntax-highlighted code blocks.
- Link previews / unfurling.
