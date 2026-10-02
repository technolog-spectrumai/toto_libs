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
`request_source` is the request's method, path, user agent and address. The
path keeps no secret: what the route captured as one becomes `[token]` — by
the error mail's rule (`toto.core.error_reports.path_secrets`, 2026-10-01): a
vault peer's grant id and magic token, and on any route a value captured
under a name like `token` — and so does a password-reset token; any other
UUID becomes `[uuid]`. The address is `toto.core.client_ip`'s,
nginx's `X-Real-IP` from a trusted proxy (2026-09-30). Rows written before
then name `X-Forwarded-For`'s first entry, which the client could write; they
stay as they are, because the digest covers them.
`suppress_audit()` switches the chain off for a block — bulk loaders and
tests only, never a setting.

**A member's own records** (2026-09-30): `queries.member_auth_records(user,
days=30)` is the one read outside the staff pages — My account's "Recent
sign-ins". It answers the `AUTH.*` records whose actor is the member, whose
subject is their account, or (a refused sign-in, a pause) whose typed name is
their username or e-mail address, any case; a pause of a whole address names
no one and is nobody's. `queries.records_about(user, also=())` (2026-10-01)
is the other half of their data export (`toto.core.personal_data`): the
records about them that somebody ELSE wrote — their account as the subject,
a refused sign-in or a pause naming them, a socialhub record carrying their
account's id in `metadata["user"]` (a community or clearance given or taken,
their data copy, their erasure request declined), and whatever the caller
adds (`also`: their profile, their applications and the references asked for
them). The export leaves out each one's `request_source` and the address a
pause names: those are the other side's.

## Who writes to it

| App label | Actions | Where |
|---|---|---|
| `auth` | `AUTH.LOGIN`, `AUTH.LOGOUT`, `AUTH.LOGIN_FAILED` (the attempted username only, `success=False`), `AUTH.ACCOUNT_CREATED`, `AUTH.ACCOUNT_ACTIVATED`/`_DEACTIVATED`, `AUTH.STAFF_GRANTED`/`_REVOKED`, `AUTH.SUPERUSER_GRANTED`/`_REVOKED` | `identity.py` (2026-09-28), from Django's own signals: every door — the login form, the SSO provider, a lockout, the admin, the membership flow, a management command |
| `auth` | `AUTH.LOCKED` (`success=False`): the sign-in lockout paused a name at an address (`scope: account_address`) or a whole address (`scope: address`) — the typed name, the address, the failures and the minutes, never a password; recorded once per pause. A try the lockout refused is an `AUTH.LOGIN_FAILED` with `refused` (`delay`, `locked`, `address_locked`), at most once a minute per name and address — a paused guesser costs no hashing, so a record per knock would fill the chain. `AUTH.UNLOCKED` (`source: console`): `manage.py unlock_signin` lifted a pause | `identity.on_signin_locked` / `on_signin_unlocked`, called by `toto.core.signin_lockout` and its command (2026-09-30) |
| `auth` | `AUTH.TOKEN_REFUSED` (`success=False`): a session key presented as an API Bearer or to the WebSocket (after the `toto.bearer` subprotocol, or as `?token=`) named an account and was refused — `inactive`, `no_account`, `backend_refused`, `backend` or `session_hash` (the password changed), with the door; never the key, recorded once because the session is ended | `identity.on_token_refused`, called by `toto.api.tokens` (2026-09-30): Django sends no signal for it |
| `auth` | `AUTH.PASSWORD_CHANGED`: a member changed their own password on My account — `sessions_ended`, how many other sign-ins (desktop tokens included) were ended with it; never a password. `AUTH.PASSWORD_RESET`: a password set through a reset link — `flow` `email` or `recovery`, and `sessions_ended` (every sign-in of the account, ended with it, 2026-10-01); never the link | `identity.on_password_changed` (socialhub `views/account.py`) and `on_password_reset` (`sso_core.password_reset`), 2026-09-30: Django sends no signal for either |
| `auth` | `AUTH.EMAIL_CHANGE_REQUESTED`: a member asked on My account to move to a new e-mail address — `new_email`, masked (`j***@example.org`). `AUTH.EMAIL_CHANGED`: they opened the mailed link, signed in — `old_email` and `new_email`, masked, and `sessions_ended` (their other sign-ins, ended with it). Never the link or its token | `identity.on_email_change_requested` / `on_email_changed` (socialhub `email_change`, 2026-09-30) |
| `auth` | `AUTH.KEY_STORE_CREATED`: a member created their own key store on My account — `strongbox_id` only; never the passphrase, the salt or a key | `identity.on_key_store_created` (socialhub `views/account.py`, `toto.gervazy.personal`), 2026-10-01 |
| `auth` | `AUTH.SESSION_ENDED`: a member ended one of their own sessions on My account — its `kind` (`browser`/`token`) and `session_id` (the `UserSession` row's id). `AUTH.SIGNED_OUT_EVERYWHERE`: every session but the one in use — `sessions_ended`. Never a session key | `identity.on_session_ended` / `on_signed_out_everywhere` (socialhub `views/account.py`, 2026-09-30) |
| `socialhub` | `SOCIALHUB.COMMUNITY_CREATED`/`_CHANGED`/`_DELETED`, `MEMBER_ADDED`/`_REMOVED` (both sides of `Person.communities`, a `clear()` included), `SENIOR_ADDED`/`_REMOVED`, `PRIVILEGE_CHANGED`/`_REMOVED`, `APPLICATION_SUBMITTED`/`_<STATUS>`/`_RENEWED` (applied again after it lapsed, 2026-10-01), `REFERENCE_REQUESTED`/`_GIVEN`/`_DECLINED`; `CLEARANCE_CREATED`/`_CHANGED`/`_DELETED` and `CLEARANCE_MEMBER_ADDED`/`_REMOVED` (both sides of `Person.clearances`, 2026-09-29) — communities and clearances are recorded apart | `toto.socialhub.audit` (2026-09-28) |
| `socialhub`, `core` | Data protection (2026-10-01, RODO): `PRIVACY.NOTICE_PUBLISHED` (each text's length, never the text) and `PRIVACY.NOTICE_ACCEPTED` (the version, on the membership application); `PRIVACY.EXPORT_REQUESTED`/`_READY`/`_FAILED` (counts and the vault file's id, never contents; `_READY` with source `console` from `export_user`); `PRIVACY.ERASURE_REQUESTED`/`_DECLINED`/`_DONE` (a note's length, never the note); `PRIVACY.HOUSEKEEPING` (`core`): one record a night, counts only — sessions cleared, sign-in rows dropped, applications pruned or kept, accounts deleted — never an address, a username or an e-mail | `toto.socialhub.audit`; `toto.core.housekeeping`, `export_user` |
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
`test_identity.py`; socialhub's `tests_audit.py` and `tests_account_signins.py` (`member_auth_records`); core's `tests_records_about_you.py` (`records_about`); the refused tokens in
`toto/api/tests/test_token_resolution.py`; the sign-in pauses in
`toto/core/tests_signin_lockout.py`.
