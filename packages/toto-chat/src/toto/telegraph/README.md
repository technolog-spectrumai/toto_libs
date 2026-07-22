# Telegraph — Real-time Chat

Chat backend for the Enigma app — Django Channels WebSockets plus a Bearer-token JSON
API. Enigma exposes chat as **two tabs with different security models**:

| Tab | Model | Transport | History | Who can read |
|-----|-------|-----------|---------|--------------|
| **P2P** | "Signal" | iroh QUIC, end-to-end **MLS** (rotor) | none (ephemeral) | members only (E2E) |
| **Relay / Forum** | "Discord" | WebSocket over **TLS** | **persistent, readable, per-room TTL (24h default)** | server + members |

This app implements the **Relay/Forum (Discord)** side. The P2P (Signal) side lives in the
edge client (iroh + rotor) and is unchanged.

> **Cryptography for auditing is in [crypto.md](crypto.md)** — the at-rest envelope, the
> end-to-end secure-on-send scheme, and how E2E MLS works.

## Why the relay tab is not end-to-end

We want Discord-style readable history: a member — including one who **just joined** — opens
a channel and sees prior messages. MLS forward secrecy makes that impossible for stored
ciphertext (old messages can't be decrypted later, even by members). Discord's answer is
pragmatic: **TLS in transit, the server can read messages**, so it can serve history to
anyone. We adopt that and encrypt the stored messages **at rest** with
[gervazy](../gervazy/README.md) so a stolen DB/backup is useless.

The privacy escape hatch is **secure-on-send** — a per-room **Secure** switch in the forum
header. When on, each message goes **end-to-end from compose time** under a member-held key
the server never sees, so the server never gets its plaintext at all. Secure messages render
inline; members decrypt them, non-holders see a locked placeholder. E2E but **not**
forward-secret (that's the P2P tab).

See [crypto.md](crypto.md) §2 for secure-on-send.

## Data model ([models.py](models.py))

- `TelegraphChannel` — `message_ttl_seconds` (default 86400 = 24h), `dek` FK to the channel's
  gervazy data key.
- `TelegraphMessage` — a persisted relay message, with `created_at`/`expires_at`, purged after
  TTL. `encryption="at_rest"` rows are encrypted under the channel DEK (server-readable);
  `encryption="e2e"` rows are **secure-on-send** — opaque `ciphertext` + `iv` + `pin_key_id`,
  encrypted on the client under a member-held key the server can't read.

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/telegraph/api/health/` | Service health check |
| POST | `/telegraph/api/login/` | Login → returns session token |
| POST | `/telegraph/api/logout/` | Logout |
| GET | `/telegraph/api/me/` | Current user profile |
| GET | `/telegraph/api/channels/` | List all channels |
| GET | `/telegraph/api/channels/{slug}/` | Channel detail + members (incl. `username`) |
| POST | `/telegraph/api/channels/{slug}/join/` · `/leave/` · `/leave-all/` | Membership |
| POST | `/telegraph/api/channels/{slug}/upload/` · `/upload-audio/` | Relay image/voice (persisted) |

## WebSocket Message Types

| Type | Direction | Description |
|------|-----------|-------------|
| `chat_message` / `image_message` / `voice_message` | both | Relay content (plaintext over TLS, persisted) |
| `secure_message` | both | Secure-on-send: E2E ciphertext under the member-held key (server-opaque, persisted) |
| `chat_history` | server→client | History batch replayed on connect (at-rest decrypted server-side; e2e raw) |
| `mls_*` | both | MLS handshake/app messages — opaque relay (P2P content; relay secure-key distribution) |
| `room_participants` | server→client | Active participant list |
| `system_error` | server→client | Error notification |

## At-rest vault ([vault.py](vault.py))

Channel DEKs live in one **telegraph system strongbox**, unlocked server-side by the
`TELEGRAPH_VAULT_PASSWORD` setting (mirrors `sso_master`'s `SSO_VAULT_PASSWORD`). Key
hierarchy and threat model are in [crypto.md](crypto.md).

## Operations

```bash
# One-time per deployment (creates the system strongbox + first data key):
TELEGRAPH_VAULT_PASSWORD=<secret> python manage.py telegraph_init_vault

# Purge expired messages (faros has no celery — run from cron, e.g. every 15 min):
python manage.py telegraph_purge_expired          # --dry-run to preview
```

- Set `TELEGRAPH_VAULT_PASSWORD` in the server environment (faros + portal). **If unset,
  chat still works but history is disabled** (messages send live, nothing is persisted).
- Per-room retention is `TelegraphChannel.message_ttl_seconds` (Django admin / shell; no
  in-app editor yet).
- **Losing `TELEGRAPH_VAULT_PASSWORD` makes at-rest history permanently unreadable.**
  Secure-on-send messages are unaffected (they use the member-held key, not this password).

## Testing

```bash
cd portal && ../venv/bin/python manage.py test toto.telegraph toto.gervazy
```
