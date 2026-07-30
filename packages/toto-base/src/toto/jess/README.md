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
> unreadable.** `deploy.py` mints it once and preserves it across redeploys for exactly
> this reason. Nothing can recover a secret without it — that is the point of encryption
> at rest, and it is worth knowing before you rotate anything.

When the vault is unavailable Jess does not crash: a send is recorded as `failed` with a
message naming the passphrase, and `email_delivery_configured()` reports that mail cannot
go out — so the "Forgot password?" link hides itself instead of promising a reset email
that could never arrive.

## The staff pages

Staff only, and `403` rather than a redirect — a polled JSON endpoint that answers with a
login page is worse than one that refuses.

| Page | What it is for |
|---|---|
| `/jess/compose/` | send a message by hand: the test page |
| `/jess/` | the outbox: what has been sent, what has not, and how much is still waiting on a worker |
| `/jess/messages/<pk>/` | one message, polled live, with the failure verbatim and a **Retry** |

There are no automatic retries. Nothing else in this suite retries either, and a silent
exponential backoff hides a misconfigured server for hours. A failed row keeps its error
and waits for a human to press the button.

## Where this is going

The outbox is deliberately the foundation of a managed auto-email service rather than a
debug log. `MailMessage.purpose` already separates the streams — `test`,
`password_reset`, `socialhub_endorsement` — which is what per-stream reporting and rate
limiting will need.

What is **not** built yet, and is not pretended to be: templates, schedules, recipient
lists, bounce handling, unsubscribe. Those arrive when something actually needs them.

---

*Greendale is fictional; the valley shots were modelled on Longsleddale in Cumbria. The
cat is real enough.*
