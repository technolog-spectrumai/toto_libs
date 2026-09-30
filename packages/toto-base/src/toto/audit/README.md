# toto.audit — the audit chain

One append-only, hash-chained log per host (`AuditRecord` in an
`AuditChain`): each record carries the hash of the one before it, so a
deleted record breaks its successor and an edited one no longer hashes to
itself. `verify_chain()` walks it; staff read it at `/audit/` (filter by app,
action, text) and verify it at `/audit/verify/`.

`services.record(action, …)` appends one record. The actor defaults to the
person at the keyboard (`AuditContextMiddleware` puts the request in
`context.py`); `SYSTEM` says "nobody did this, it happened"; metadata goes
through `sanitize`, which drops anything that looks like a secret.
`suppress_audit()` switches the chain off for a block — bulk loaders and
tests only, never a setting.

## Who writes to it

| App label | Actions | Where |
|---|---|---|
| `auth` | `AUTH.LOGIN`, `AUTH.LOGOUT`, `AUTH.LOGIN_FAILED` (the attempted username only, `success=False`), `AUTH.ACCOUNT_CREATED`, `AUTH.ACCOUNT_ACTIVATED`/`_DEACTIVATED`, `AUTH.STAFF_GRANTED`/`_REVOKED`, `AUTH.SUPERUSER_GRANTED`/`_REVOKED` | `identity.py` (2026-09-28), from Django's own signals: every door — the login form, the SSO provider, a lockout, the admin, the membership flow, a management command |
| `auth` | `AUTH.LOCKED` (`success=False`): the sign-in lockout paused a name at an address (`scope: account_address`) or a whole address (`scope: address`) — the typed name, the address, the failures and the minutes, never a password; recorded once per pause. A try the lockout refused is an `AUTH.LOGIN_FAILED` with `refused` (`delay`, `locked`, `address_locked`), at most once a minute per name and address — a paused guesser costs no hashing, so a record per knock would fill the chain. `AUTH.UNLOCKED` (`source: console`): `manage.py unlock_signin` lifted a pause | `identity.on_signin_locked` / `on_signin_unlocked`, called by `toto.core.signin_lockout` and its command (2026-09-30) |
| `auth` | `AUTH.TOKEN_REFUSED` (`success=False`): a session key presented as an API Bearer or WebSocket `?token=` named an account and was refused — `inactive`, `no_account`, `backend_refused`, `backend` or `session_hash` (the password changed), with the door; never the key, recorded once because the session is ended | `identity.on_token_refused`, called by `toto.api.tokens` (2026-09-30): Django sends no signal for it |
| `socialhub` | `SOCIALHUB.COMMUNITY_CREATED`/`_CHANGED`/`_DELETED`, `MEMBER_ADDED`/`_REMOVED` (both sides of `Person.communities`, a `clear()` included), `SENIOR_ADDED`/`_REMOVED`, `PRIVILEGE_CHANGED`/`_REMOVED`, `APPLICATION_SUBMITTED`/`_<STATUS>`, `REFERENCE_REQUESTED`/`_GIVEN`/`_DECLINED`; `CLEARANCE_CREATED`/`_CHANGED`/`_DELETED` and `CLEARANCE_MEMBER_ADDED`/`_REMOVED` (both sides of `Person.clearances`, 2026-09-29) — communities and clearances are recorded apart | `toto.socialhub.audit` (2026-09-28) |
| `wiki` | page created, updated, deleted, imported, exported, refused writes, a topic's clearances changed | zenobia's `toto.wiki.audit` / `access.py` |
| `aralia` | a PDF render's life | zenobia's `toto.aralia.audit` |
| file events | vault reads and writes, refusals included; a refused claim on a file's editing lock (403/404) as `FILE_LOCK_REFUSED` (2026-09-30) — granted claims, 423s and heartbeats stay off | `FileAuditMiddleware` |

**Deleting an account never breaks the chain** (2026-09-29). The digest
covers `actor_user_id`; the field used to be SET_NULL, so deleting any
account that had acted — and every account that signs in has — rewrote
sealed rows and verification failed. It is DO_NOTHING with no database
constraint now: the id stays as written, `actor_username` names them, and a
deleted account's id simply points at nothing. `toto.core`'s `erase_user`
(console only) records `AUTH.ACCOUNT_ERASED` itself.

**Never in the way.** The identity and community writers wrap each record in
its own savepoint and swallow (and log) a failure: a login, a signup or a
membership change never fails because the chain could not be written.
**Never a secret.** A refused login records the username it was tried with
and nothing else from the credentials.

Tests: `tests/test_chain.py`, `test_pages.py`, `test_file_audit.py`,
`test_identity.py`; socialhub's `tests_audit.py`; the refused tokens in
`toto/api/tests/test_token_resolution.py`; the sign-in pauses in
`toto/core/tests_signin_lockout.py`.
