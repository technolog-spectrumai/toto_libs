# Telegraph chat — cryptography (for auditing)

This document describes every cryptographic mechanism behind Enigma chat so it can be
reviewed. There are **three** distinct schemes:

1. **At-rest encryption of relay history** — server-readable (Discord model).
2. **End-to-end encrypted pins** — server-opaque (Signal-grade), layered on the relay tab.
3. **End-to-end MLS** — the P2P (Signal) tab and the relay pin-key control channel.

Primitives throughout: **AES-256-GCM** (12-byte nonce, 16-byte tag) for symmetric
encryption, **Argon2id** for password KDF, **MLS / OpenMLS** (via rotor) for group E2E.
No bespoke ciphers.

Threat-model summary:

| Scheme | Confidential against… | NOT confidential against… |
|--------|----------------------|---------------------------|
| Relay history (at-rest) | DB/backup theft, on-disk attacker | the server operator (holds the key) |
| E2E pins | the server operator, DB theft, the network | a member you shared the channel with |
| MLS (P2P, pin-key transport) | the server/relay, the network | current group members |

---

## 1. Relay history — encryption at rest (server-readable)

Regular relay ("Forum") messages travel **plaintext over TLS** to the server, which stores
them encrypted at rest and can decrypt them to serve readable history (including to brand-new
joiners). This is deliberately **not** end-to-end — that is what makes durable, readable
history possible. The at-rest layer protects a stolen database or backup, not against the
server operator.

It reuses [gervazy](../gervazy/README.md)'s envelope. Key hierarchy:

```
TELEGRAPH_VAULT_PASSWORD            deployment secret (env/secret store; NEVER in the DB)
        │ Argon2id (per UserStrongbox params; salt stored, password is not)
        ▼
UKEK    User Key Encryption Key — derived at runtime, never stored
        │ AES-256-GCM unwrap
        ▼
VMK     VaultMasterKey — stored AES-GCM-wrapped in the telegraph system strongbox
        │ AES-256-GCM unwrap
        ▼
DEK     one WrappedDataKey per channel — stored AES-GCM-wrapped in the DB
        │ AES-256-GCM (AAD = "telegraph:<slug>:<message-uuid>")
        ▼
TelegraphMessage.ciphertext + nonce        (the only at-rest form of a message)
```

- The **system strongbox** (`vault.SYSTEM_STRONGBOX_NAME`) is owned by an inactive service
  user and unlocked server-side by `TELEGRAPH_VAULT_PASSWORD` — the same pattern as the SSO
  signing vault (`sso_master`). No human password at request time.
- **One DEK per channel** (created lazily on first message via `GervazyCryptoSession.create_data_key`).
  Compromise of one channel's DEK does not expose others.
- **AAD binds** each ciphertext to `channel.slug + message id`, so a stored row cannot be
  replayed/relocated under a different channel or id without failing authentication (`InvalidTag`).
- The unlocked session (and thus the UKEK from Argon2id) is **cached per process**
  (`vault.open_session`) so the KDF runs once, not per message.
- **Retention:** each message gets `expires_at = created_at + channel.message_ttl_seconds`
  (default 24h). Expired rows are excluded from replay and deleted by `telegraph_purge_expired`
  (cron; faros has no celery). Deletion is the privacy control for the readable archive.
- **Key loss:** losing `TELEGRAPH_VAULT_PASSWORD` makes stored history permanently unreadable.
  If it is unset, persistence is disabled and chat falls back to live-only (no crash).

Implementation: [vault.py](vault.py), [`gervazy/crypto.py`](../gervazy/crypto.py)
(`encrypt_blob`/`decrypt_blob`/`create_data_key`), [consumers.py](consumers.py).

## 2. End-to-end pins (server-opaque)

Pinning is the privacy escape hatch: a pinned message is **end-to-end encrypted** under a
key the server never sees, so the durable artifact is not in the server's readable archive.

### The pin key
- Per channel, **32-byte symmetric** key with an opaque `pin_key_id`. Held **only by
  members**; **never sent to the server in clear**. Persisted on-device in local KV
  (`pinkey:<slug>`) — a *key* at rest, the same protection level rotor already uses for MLS
  state. It is **deliberately not forward-secret**: pins must stay readable over time.
- Generated on a member's device the first time a pin is created (`pinCrypto.randomPinKey` +
  `newPinKeyId`, WebCrypto `getRandomValues`).

### Distribution (members only, over MLS)
The pin key rides the relay channel's **MLS group** as application messages — the relay tab
keeps an MLS group purely as a members-only control channel (regular *content* is plaintext;
only the pin key is E2E here). The server relays opaque MLS ciphertext and never sees the key.

- **Bootstrap:** first pinner sends `{type:"pin_key", pin_key_id, key}` over MLS; current
  members cache it.
- **New joiner:** on entering, fetches `GET …/pins/`; if `pin_key_id` is set but it lacks the
  key, it broadcasts `{type:"pin_key_request"}` over MLS; any member responds with `pin_key`.
  Until then the joiner shows pins as a **locked placeholder**.
- **Rotation on member removal** (generate `pin_key_v2`, redistribute, encrypt new pins under
  v2; old pins remain readable by anyone who had v1) is a documented follow-up, not in v1.

### Pin content
- Client encrypts the message JSON with **AES-256-GCM** under the pin key
  (`pinCrypto.encryptPin`, random 12-byte IV) and uploads `{pin_key_id, iv, ciphertext}`.
- The server stores a `TelegraphPin` holding only those opaque fields + cleartext display
  metadata (pinner, original sender/time). It **deletes the server-readable `TelegraphMessage`
  copy** on pin, so only the E2E ciphertext persists.
- Reading: a member decrypts with the locally-held pin key; a non-holder sees the placeholder.

> Honest caveat: the server *did* see the message's plaintext in the ≤24h readable history
> before it was pinned. E2E pinning protects the **durable** record going forward (the live
> copy is deleted on pin), not that transient exposure. A "secure-on-send" mode (E2E from
> compose time, never server-readable) is a possible future addition.

Implementation: [pinCrypto.ts](../../../edge/packages/rakotis/src/pinCrypto.ts),
[useChatChannel.ts](../../../edge/packages/rakotis/src/useChatChannel.ts) (pin orchestration),
pin endpoints in [api_views.py](api_views.py).

## 3. End-to-end MLS (P2P tab + pin-key transport)

MLS (Messaging Layer Security, RFC 9420) via **rotor** (`rotor-core` Rust / `rotor-wasm` in
the webview) provides group end-to-end encryption with **forward secrecy** and
**post-compromise security**:

- Each member generates a **KeyPackage** (long-term + ephemeral keys). A member is added by
  an existing member committing an `Add`, producing a **Welcome** that bootstraps the new
  member into the current epoch.
- The group shares a per-epoch secret from which **AEAD message keys** are derived via a
  ratchet. **Commits** advance the epoch (on add/remove/update); past-epoch secrets are
  dropped — this is the forward secrecy that makes stored MLS ciphertext undecryptable later
  (and is precisely why relay *history* uses scheme #1 instead).
- Application messages are encrypted with per-sender ratchet keys; the relay/server only ever
  sees opaque `mls_app` ciphertext and routing fields (`consumers.handle_opaque_mls_message`
  forwards nothing else — no identity correlation at the app layer).
- **P2P tab:** content rides MLS end-to-end over iroh QUIC (no server). **Relay tab:** content
  is *not* MLS (see #1); MLS is retained only to distribute the pin key (#2).

The MLS credential is the device's stable chat identity (`mlsIdentity`). Group state is
persisted locally by rotor.

Implementation: rotor (`toto/rotors/rotor_core`, `rotor_wasm`), client MLS orchestration in
[useChatChannel.ts](../../../edge/packages/rakotis/src/useChatChannel.ts), opaque relay in
[consumers.py](consumers.py).

---

## What the server can and cannot read

| Artifact | Server can read? |
|----------|------------------|
| Relay message in transit | Yes (TLS terminates at the server) |
| Relay message at rest (`TelegraphMessage`) | Yes, with `TELEGRAPH_VAULT_PASSWORD` (Discord model) |
| Pinned message (`TelegraphPin`) | **No** — opaque ciphertext, key is member-only |
| Pin key | **No** — only ever inside MLS ciphertext + on member devices |
| P2P (Signal) messages | **No** — end-to-end MLS, no server in the path |
