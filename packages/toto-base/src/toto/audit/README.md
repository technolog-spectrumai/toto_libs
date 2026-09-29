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
| `socialhub` | `SOCIALHUB.COMMUNITY_CREATED`/`_CHANGED`/`_DELETED`, `MEMBER_ADDED`/`_REMOVED` (both sides of `Person.communities`, a `clear()` included), `SENIOR_ADDED`/`_REMOVED`, `PRIVILEGE_CHANGED`/`_REMOVED`, `APPLICATION_SUBMITTED`/`_<STATUS>`, `REFERENCE_REQUESTED`/`_GIVEN`/`_DECLINED` — every community record says whether it is a circle | `toto.socialhub.audit` (2026-09-28) |
| `wiki` | page created, updated, deleted, imported, exported, refused writes, circles and owners changed | zenobia's `toto.wiki.audit` / `access.py` |
| `aralia` | a PDF render's life | zenobia's `toto.aralia.audit` |
| file events | vault reads and writes | `FileAuditMiddleware` |

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
`test_identity.py`; socialhub's `tests_audit.py`.
