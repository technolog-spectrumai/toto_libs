# toto.jess — the platform's post

> *"Postman Pat, Postman Pat, Postman Pat and his black and white cat…"*

**Jess** is the black-and-white cat who rides in the front of Postman Pat's red Royal
Mail van through the valley of Greendale — stop-motion, fifteen minutes an episode, first
broadcast on the BBC in 1981, written by John Cunliffe and animated by Ivor Wood. Pat
delivers the post. Jess comes along for every round.

This app is named for the cat rather than the postman on purpose. It does not write the
letters — the platform does that. Jess carries them, knows the route, and is the one who
notices when a delivery did not happen.

---

## What it is

A **dynamic email service**: the outbound mail transport, configured in the Django admin
instead of in environment variables, with the SMTP password encrypted at rest and every
message recorded on its way out.

Three things it owns:

* **`EmailProvider`** — how mail leaves. Several rows may exist; exactly one is `active`.
  Switching provider is flipping a flag, and the previous row stays for a rollback. The
  password is a `gervazy.EncryptedSecret`, so it is AES-256-GCM at rest and never
  readable from the database alone.
* **`MailMessage`** — the outbox. One row per message, `queued → sending → sent/failed`,
  with the provider's own error text kept verbatim on failure.
* **`JessEmailBackend`** — a Django email backend that **queues** rather than sends. Point
  `EMAIL_BACKEND` at it and every existing sender in the platform — password reset
  included — becomes asynchronous and visible, with no caller changes.

## Why the queue is not optional

An SMTP conversation is a network call to somebody else's server. Doing that inside a web
request means a worker is held for as long as a third party feels like taking, which is
the exact failure the platform's `statement_timeout` ladder exists to prevent. So Jess
never sends in a request: the backend writes a row, hands it to Celery, and returns.

The honest cost, stated plainly: **on a host with no Celery worker, mail queues and never
leaves.** That is why Jess is flag-gated rather than a core app, and why the outbox page
exists — a message that has not gone out is visible rather than lost.

## Why the password is encrypted rather than in the environment

Because the alternative leaks. `deploy.py` copies every config env key verbatim into
`.env` with no redaction, so an SMTP password put there sits in plaintext on the host, in
the deploy config, and in whatever git repository that config lives in. Encrypting it in
the database with a key that lives in one place is strictly better, and the mechanism was
already here: it is the same envelope that protects the SSO signing key.

Jess unlocks its own strongbox, `jess-system`, with `JESS_VAULT_PASSWORD`.

> **If that passphrase is lost or regenerated, every stored secret is permanently
> unreadable.** In the default mode `deploy.py` mints it once and preserves it across
> redeploys for exactly this reason. Nothing can recover a secret without it — that is the
> point of encryption at rest, and it is worth knowing before you rotate anything.

When the vault is unavailable Jess does not crash: a send is recorded as `failed` with a
message naming the passphrase, and `email_delivery_configured()` reports that mail cannot
go out — the reset page then serves patron-approved recovery (`sso_core.password_reset`'s
flow 2) instead of promising a reset email that could never arrive. (The "Forgot
password?" link used to hide itself outright; since the two-flow rework it always has
somewhere honest to lead.)

## Manual-release custody (`JESS_MANUAL_RELEASE=1`)

Encryption at rest only helps against someone who has the database but **not** the key. In
the default mode the key (`JESS_VAULT_PASSWORD`) is in `.env` and the worker decrypts
unattended — so anyone who can read both the database and the environment has both halves.
Manual-release mode closes that: **no passphrase lives on the server at all.**

* `deploy.py` writes **no** `JESS_VAULT_PASSWORD`. An admin sets it once through
  **`/jess/vault/set-up/`**, choosing it — it is never stored, and cannot be recovered.
* Every outgoing message — password resets included — is recorded **`held`**, not
  dispatched. Nothing sends on its own.
* An admin **releases** held mail at **`/jess/release/`** by typing the passphrase, which
  decrypts the SMTP password in memory for that one send and is then dropped. The
  passphrase is never handed to Celery (it would sit in the broker); the release sends
  inline, one connection for the batch, capped per request.
* The passphrase and the SMTP password are both rotatable — **`/jess/vault/passphrase/`**
  re-keys the vault (re-wrapping only the master key, so no stored secret is re-encrypted
  or exposed) and **`/jess/vault/password/`** replaces the SMTP password.

The honest cost: **password resets are no longer self-service.** A reset requested at 3am
sits held until an admin releases it. The outbox shows the held count so the backlog is
impossible to miss. A held **password-reset link is a one-time credential**, so its body
is never shown on any staff page — an admin releases it (sends it) without being able to
read it.

## Session custody (no stored password at all)

The third mode, and the only one with **nothing at rest**: the provider row carries host,
port and username but no secret. A staff member types the account password once per login
session on `/jess/unlock/` (proven with one SMTP handshake before it is held); it lives in
that web process's memory (`credentials.py`) — never the database, never `request.session`
(DB-backed on real hosts), never disk — and is gone on logout, expiry or restart.

Because no other process can see that memory, mail that depends on it **sends inline in
the web request** (`delivery.send_single_now`, used by the password-reset flow) rather
than through the Celery queue. And because the store is per process, a multi-worker
deployment holds the credential only in the worker that took the unlock POST — the
optional `JESS_EMAIL_PASSWORD` env bootstrap (a stated trade: plaintext in `.env`, read
once into memory at boot) is what arms every worker at once. When no process holds a
credential, the reset page falls back to patron-approved recovery on its own.

## The staff pages

Staff only, and `403` rather than a redirect — a polled JSON endpoint that answers with a
login page is worse than one that refuses.

| Page | What it is for |
|---|---|
| `/jess/account/` | set up the email account — SMTP (and optional IMAP) host, sign-in, password — and send a test to prove it works, without the Django admin |
| `/jess/compose/` | send a message by hand (manual mode: type the passphrase to send now, or leave it held) |
| `/jess/` | the outbox: what has been sent, what has not, and — in manual mode — how much is held |
| `/jess/messages/<pk>/` | one message, polled live, with the failure verbatim and a **Retry** (or **Release** if held) |
| `/jess/inbox/` · `/jess/inbox/<pk>/` | received mail, fetched on demand, and one message read (with a **Reply**) |
| `/jess/release/` | manual mode: type the passphrase to release held mail |
| `/jess/vault/set-up/` · `/jess/vault/password/` · `/jess/vault/passphrase/` | manual mode: initialise the vault, set the SMTP password, change the passphrase |

## Receiving (the inbox)

The same account can be read as well as written: give an `EmailProvider` an IMAP host on
the account page and `/jess/inbox/` can **fetch** the mail it has received into an
`InboundMessage` row, list it, and let a staff member **reply** — the reply goes out
through the ordinary outbox path, threaded with `In-Reply-To`/`References`.

Fetching is **on demand, never a background poll**, and that is a deliberate consequence
of manual-release custody: a scheduled job would have to decrypt the IMAP password with no
human present, and in manual-release mode there is no server-side passphrase for it to use.
So a person triggers the fetch — and under manual release types the passphrase — exactly
as they do to release outbound mail. It also means the inbox works on a host with no Celery
worker (the fetch runs in the request), and is bounded per fetch so the request stays short.
Dedup is by `Message-ID`, so fetching twice is safe.

There are no automatic retries. Nothing else in this suite retries either, and a silent
exponential backoff hides a misconfigured server for hours. A failed row keeps its error
and waits for a human to press the button.

## Where this is going

The outbox is deliberately the foundation of a managed auto-email service rather than a
debug log. `MailMessage.purpose` already separates the streams — `test`, `manual`,
`password_reset`, `socialhub_endorsement` — which is what per-stream reporting and rate
limiting will need.

What is **not** built yet, and is not pretended to be: templates, schedules, recipient
lists, unsubscribe. Bounce handling is the natural next thing, and now cheap: a bounce is
just an `InboundMessage` the inbox already fetches, correlated back to its outbound
`MailMessage` through `headers`/`purpose`. Those arrive when something actually needs them.

---

*Greendale is fictional; the valley shots were modelled on Longsleddale in Cumbria. The
cat is real enough.*
