# Forum security

What protects a conversation on this forum, in transit and at rest, and what
the three kinds of room add. Written 2026-09-25 with the change that
introduced them; `zenobia/forum_security.md` in the host carries the
deployment specifics.

## 1. What was there before

- **In transit**: TLS 1.2/1.3 terminated by nginx (deploy.py
  `build_nginx_conf`, HSTS, nosniff, CSP), then HTTP and the WebSocket upgrade
  to the ASGI server. The socket authenticates with the session cookie, or
  `?token=<session key>` for the desktop client (`toto.api.middleware`).
- **At rest**: every message body in plaintext in `forum_forummessage.body`;
  attachments as plain files under `FORUM_ATTACHMENT_ROOT`, outside the media
  volume nginx serves, handed out only by a membership-checked view.
- **Access**: any signed-in member could list and join any room; membership
  (`ForumMember`) gated reading and sending, re-checked on the open socket.
- **Missing**: no private rooms, no rate limit, no origin check on the socket
  (a page on another origin could open it with the member's cookies), the JSON
  doors accepted cookie writes from other sites, and nothing was metered.
- An earlier design sealed every message under a per-room gervazy key and was
  removed (store.py): history capped at 24 h, a lost deploy secret lost all
  history, and bodies could not be searched.

## 2. Gervazy: what its trust model fits

Gervazy is **custody**: a password (a person's, or a deploy secret for a
service box) → Argon2id → a key-encryption key → a master key → data keys →
AES-256-GCM. It has no per-person encryption key pairs, no directory where
one member could find another's public key, no browser key store, and
`PersonSigningKey` is Ed25519 for signatures only. So it does **not** fit
identity or key distribution between people, and end-to-end encryption would
need all of those built first. It fits exactly one thing here: holding room
keys at rest on the server, which is what it now does.

The reference the request pointed at (pgp-sms) is client-side OpenPGP:
browser-generated key pairs, public keys exchanged as `.asc`, ciphertext
prefixed `PGP1:`. It was studied and not copied, for the reasons above.

## 3. Room kinds

Chosen when a room is made, fixed for its life (only a password may change).
There are no invitations (2026-09-28): a password will do. Invite-only rooms
from before became password rooms with no password (migration 0007) — their
members stayed, nobody new joins until the creator or staff set one:

| Kind | Who joins | At rest | Search |
|---|---|---|---|
| open (every room from before) | any member | plaintext | yes |
| password | whoever knows the password | plaintext unless encrypted | yes unless encrypted |
| **encrypted** (any of the above) | as above | AES-256-GCM ciphertext | **no** |
| **temporary** (any of the above) | as above | as above | as above; the room and everything in it are deleted at expiry |

The UI says which on the room list, the room header and the create form
(`_room_badges.html`); an encrypted room shows a notice, hides the search box,
and the search page counts the encrypted rooms it skipped. Each room's
**Security** tab (2026-09-28, `room_security.html`) says all of it to its
members in one place — who may join and how, whether a password is set, the
KDF costs and the guessing limits, encryption at rest and the room key's
version, the room's lifetime, the mana a message costs, what protects it in
transit — and is where the room's creator or staff set or change the
password (the Members tab no longer does).

## 4. Keys

```
FORUM_VAULT_PASSWORD ─Argon2id▶ UKEK ▶ VMK ▶ DEK   (strongbox "forum-rooms")
                                              └─AES-GCM▶ room key ─AES-GCM▶ messages, attachments
room password ─Argon2id(64 B)▶ [wrap key | verifier]
                                  └─AES-GCM▶ room key                (password rooms)
shared cache, TTL = expiry ─▶ room key                              (temporary rooms)
```

- A 256-bit room key per encrypted room (`rooms.create_room_key`), stored only
  wrapped (`ForumRoomKey`), AAD `toto:forum:roomkey:v1:<room>`.
- The room password is never stored. One Argon2id derivation (64 MiB, t=3,
  p=4 by default; per-room costs stored) yields two independent halves: a
  verifier (stored, compared in constant time) and a wrap key (never stored).
- A temporary room's key is never written to the database: it lives in the
  shared cache until the room expires (crypto-shredding), and a host whose
  cache is per-process refuses to make one.
- The key never reaches the browser. A process unwraps it on demand and keeps
  it in memory by room and version.

## 5. Sealing

`sealing.py`: AES-256-GCM through `cryptography` (gervazy's
`aes_gcm_encrypt`), a fresh random 96-bit nonce per seal, AAD
`toto:forum:{msg|att}:v1:<room>:<message>` binding each frame to its room and
message, framed `0x01 || nonce || ciphertext+tag`. The sealed body is its own
column (`body_sealed`); `body` stays empty, so search, the admin and any
filter over `body` find nothing. An edit re-seals with a fresh nonce. No
primitive is home-made.

## 6. Recovery matrix

| Lost | Persistent room | Password room | Temporary room |
|---|---|---|---|
| FORUM_VAULT_PASSWORD | unreadable (the custody rule) | members recover with the password (`rooms.open_key_with_password`) | unaffected until expiry |
| the room password | unaffected | the platform still reads it; the owner sets a new one | as for a persistent room |
| the cache (restart, eviction) | unaffected | unaffected | unreadable at once — early crypto-shredding, by design |

`FORUM_VAULT_PASSWORD` is minted once by deploy.py and kept, like every other
`*_VAULT_PASSWORD`; it belongs in the operator's secret custody.

## 7. Membership, expiry, retention, export

- Reading and sending need an active membership and an unexpired room
  (`permissions.can_read` asks the clock); the socket re-checks on every
  membership change and closes 4403, and an expired room's socket gets
  `room_closed` and 4410.
- Leaving deactivates the membership; rejoining a password room needs the
  password again. Replay decrypts everything the room key opens for any active
  member: membership, not the time one joined, is the boundary.
- Expiry (`expiry.py`, every five minutes): messages, attachment bytes and
  polls through the cleanup machinery (a run triggered by `expiry` that keeps
  the room's name), then the key, then the sockets, then the room.
- Retention (staff-set, nightly) applies to encrypted rooms exactly as to
  ordinary ones; it deletes rows and keeps the room key.
- The staff room archive is the plaintext copy somebody chooses to take: it
  decrypts bodies with the room key and names sealed attachments as left out.
- Attachments are not in any backup until the host's media sidecar includes
  `FORUM_ATTACHMENT_ROOT` (see the host document).

## 8. Rate limits

`toto.core.ratelimit`: a fixed-window counter in the shared cache.
Defaults (`FORUM_RATE_LIMITS` overrides): password joins 5 per person per room
and 30 per room per 5 minutes (each attempt is a 64 MiB Argon2id), messages 20
per 10 s per sender (three refusals in a row close the socket 4429), API
posts 30 per minute. A cache outage fails open and logs; nginx `limit_req` is
the backstop.

## 9. Cross-site and origin

The socket is wrapped in `toto.api.ws_origin.TotoOriginValidator` by the host
(an Origin must be an allowed host; no Origin, as from the desktop client, is
allowed). Every forum JSON write refuses a cookie-authenticated request that a
browser labelled `Sec-Fetch-Site: same-site|cross-site`
(`toto.api.fetch_metadata`); a Bearer token passes.

## 10. Billing

`billing.py`, the platform charge ladder:

| Metric | Pool | One unit |
|---|---|---|
| `forum.message` | security | an ordinary message stored |
| `forum.encrypt` | compute | a message sealed |
| `forum.room_key` | compute | an encrypted room's key made |

Quota and mana are checked before anything is stored; the message insert and
the event-gated charge run in one transaction, so a refused charge stores no
message and a failed store charges nothing; the usage event's idempotency key
(`<metric>:<id>`) makes a retry charge nothing more. An empty pool refuses the
send with the pool's own sentence — never a negative balance. Rates are Tariff
rows seeded once (`ingress_mana`), overridable with `MANA_PRICES`; the composer
shows the price with `{% price_hint %}`.

## 11. What this does not do

- The server can read every encrypted room: that is the trust model chosen,
  and the reason it is called encryption **at rest**.
- The WebSocket still accepts a session key in the query string (open risk,
  technology.md).
- Encrypted attachments are not in the room archive.
