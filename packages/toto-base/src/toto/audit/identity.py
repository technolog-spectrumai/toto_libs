"""Who came in, and who was let in: identity events on the chain (2026-09-28).

The chain recorded documents and money and said nothing about people coming
and going. Now it does, from Django's own signals — so every door is covered
at once: the login form, the SSO provider, the sign-in lockout, the admin, the
membership flow, a management command.

| action | when |
|---|---|
| `AUTH.LOGIN` | a session starts (`user_logged_in`) |
| `AUTH.LOGOUT` | a session ends (`user_logged_out`) |
| `AUTH.LOGIN_FAILED` | credentials refused, a lockout included (`user_login_failed`); `success=False`, the attempted username only; a try the sign-in lockout refused carries `refused` (`delay`, `locked`, `address_locked`) and is recorded at most once a minute per name and address |
| `AUTH.LOCKED` | the sign-in lockout paused a name at an address, or a whole address (`toto.core.signin_lockout`, 2026-09-30); `success=False`, the scope, the typed name, the address, the failures and the minutes |
| `AUTH.UNLOCKED` | a pause lifted from the console (`manage.py unlock_signin`); the name, the address or `all` |
| `AUTH.TOKEN_REFUSED` | a session key presented as an API or WebSocket token named an account and was refused (`toto.api.tokens`, 2026-09-30); `success=False`, the door and the reason only |
| `AUTH.PASSWORD_CHANGED` | a member changed their own password while signed in (My account, 2026-09-30); `sessions_ended` — how many other sign-ins went with the old one |
| `AUTH.SESSION_ENDED` | a member ended one of their own sessions from My account (`toto.core.user_sessions`, 2026-09-30); its `kind` (`browser` or `token`) and `session_id`, the row's id — never the key |
| `AUTH.SIGNED_OUT_EVERYWHERE` | a member ended every session but the one in use (My account, 2026-09-30); `sessions_ended` |
| `AUTH.PASSWORD_RESET` | a password set through a reset link (`sso_core.password_reset`, 2026-09-30); `flow` is `email` (the mailed link) or `recovery` (a patron's one-time link) |
| `AUTH.ACCOUNT_CREATED` | a `User` row is created, by whatever door |
| `AUTH.ACCOUNT_ACTIVATED` / `_DEACTIVATED` | `is_active` changes |
| `AUTH.STAFF_GRANTED` / `_REVOKED` | `is_staff` changes |
| `AUTH.SUPERUSER_GRANTED` / `_REVOKED` | `is_superuser` changes |

**Never a secret.** A failed login records the username it was tried with and
nothing else from the credentials; the metadata goes through ``sanitize``
besides. **Never in the way.** A record that cannot be written is logged and
dropped — a login or a signup never fails because the chain did.
The actor is the person doing it when a request is at hand (the audit
context), the account itself for its own login, and the system otherwise.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save, pre_save

log = logging.getLogger("toto.audit")

APP_LABEL = "auth"

#: The account flags whose change is an event, and the words for each way.
FLAGS = {
    "is_active": ("account_activated", "account_deactivated"),
    "is_staff": ("staff_granted", "staff_revoked"),
    "is_superuser": ("superuser_granted", "superuser_revoked"),
}


def _record(action, user=None, *, username="", **kwargs):
    from django.db import transaction

    from .services import record

    try:
        # Its own savepoint: a failed insert must not poison the transaction
        # the login or the signup is running in.
        with transaction.atomic():
            return record(f"auth.{action}", app_label=APP_LABEL, object_type="auth.user",
                          object_id=str(user.pk) if user is not None and user.pk else "",
                          description=(user.get_username() if user is not None else username)[:150],
                          **kwargs)
    except Exception:  # noqa: BLE001 - the chain never breaks a login
        log.exception("audit: could not record auth.%s", action)
        return None


def on_login(sender, request, user, **kwargs):
    _record("login", user, actor_user=user, request=request,
            metadata={"backend": getattr(user, "backend", "") or ""})


def on_logout(sender, request, user, **kwargs):
    # Django sends this for a visitor who was never signed in too: that ends
    # no session, and a record of nobody leaving is noise.
    if user is None or not getattr(user, "pk", None):
        return
    _record("logout", user, actor_user=user, request=request)


def on_login_failed(sender, credentials, request=None, **kwargs):
    from .services import SYSTEM

    credentials = credentials or {}
    attempted = str(credentials.get("username") or credentials.get("email") or "")[:150]
    metadata = {"username": attempted}
    # The sign-in lockout's refusal, when it was that (2026-09-30). A paused
    # guesser's try costs no hashing, so a record per knock would be a cheap
    # way to fill the chain: the lockout marks the first knock each minute
    # per name and address, and only that one is written.
    refused = getattr(request, "signin_refusal", None) if request is not None else None
    if refused is not None:
        if not getattr(refused, "record", True):
            return
        metadata["refused"] = str(getattr(refused, "reason", ""))
    _record("login_failed", username=attempted, actor_user=SYSTEM, request=request,
            success=False, metadata=metadata)


def on_signin_locked(*, scope, address, failures, minutes, username="", request=None):
    """The sign-in lockout paused a sign-in (2026-09-30).

    Called by ``toto.core.signin_lockout`` once, when a count reaches its
    threshold. ``scope`` is ``account_address`` (this name from this address)
    or ``address`` (every name from it). The name is the one typed, whether or
    not an account has it — the record does not look it up — and nothing else
    from the credentials. The actor is the system: whoever was guessing is not
    proven to be anybody.
    """
    from .services import SYSTEM

    attempted = str(username or "")[:150]
    metadata = {"scope": scope, "address": str(address), "failures": int(failures),
                "minutes": int(minutes)}
    if attempted:
        metadata["username"] = attempted
    _record("locked", username=attempted or str(address), actor_user=SYSTEM, request=request,
            success=False, metadata=metadata)


def on_signin_unlocked(*, username="", address="", everything=False):
    """A pause lifted at the console (``manage.py unlock_signin``, 2026-09-30).

    Returns the record, or None when it could not be written — the command
    says so rather than pretending."""
    from .services import SYSTEM

    metadata = {}
    if everything:
        metadata["all"] = True
    if username:
        metadata["username"] = str(username)[:150]
    if address:
        metadata["address"] = str(address)
    return _record("unlocked", username=str(username or address or "all")[:150],
                   actor_user=SYSTEM, source="console", metadata=metadata)


def on_token_refused(user, *, account_id, reason, door, request=None):
    """A token that named an account and was refused (2026-09-30).

    Called by ``toto.api.tokens``, not a signal: Django has none for a session
    that fails its check. ``reason`` is one of ``inactive``, ``no_account``,
    ``backend_refused``, ``backend`` or ``session_hash`` (the password changed
    since the sign-in); ``door`` is ``api`` or ``websocket``. The key itself
    is a credential and is never recorded, nor anything derived from it. The
    actor is the system: whoever presented the key, it is not proven to be
    the account.
    """
    from .services import SYSTEM

    metadata = {"door": door, "reason": reason}
    if user is None:
        # The account is gone; its id is all that is left to name it by.
        metadata["account_id"] = str(account_id)
    _record("token_refused", user, actor_user=SYSTEM, request=request,
            success=False, metadata=metadata)


def on_password_changed(user, *, request=None, sessions_ended=0):
    """A member changed their own password, signed in (2026-09-30).

    Called by the My account view, not a signal: Django sends none for a
    password change, and ``set_password`` is also how an account is made, a
    test user is set up and a reset lands — this record is the one door where
    the member typed the old password and chose the new. Neither password,
    nor anything derived from them, is recorded.
    """
    return _record("password_changed", user, actor_user=user, request=request,
                   metadata={"sessions_ended": int(sessions_ended)})


def on_session_ended(user, *, kind, session_id, request=None):
    """A member ended one of their own sessions from My account (2026-09-30).

    ``session_id`` is the ``UserSession`` row's id, which is all the page
    ever names a session by; the session key is a credential and neither it
    nor anything derived from it is recorded.
    """
    return _record("session_ended", user, actor_user=user, request=request,
                   metadata={"kind": str(kind), "session_id": int(session_id)})


def on_signed_out_everywhere(user, *, sessions_ended=0, request=None):
    """A member ended every session but the one in use (2026-09-30)."""
    return _record("signed_out_everywhere", user, actor_user=user, request=request,
                   metadata={"sessions_ended": int(sessions_ended)})


def on_password_reset(user, *, flow, request=None):
    """A password set through a reset link (2026-09-30).

    ``flow`` is ``email`` or ``recovery``. The actor is the account: whoever
    held the link spoke as it. The link and its token are credentials and are
    never recorded.
    """
    return _record("password_reset", user, actor_user=user, request=request,
                   metadata={"flow": str(flow)})


def before_user_saved(sender, instance, update_fields=None, **kwargs):
    """Remember the flags as they were — only when a flag may be changing
    (a login's ``last_login`` save does not pay for a read)."""
    if not instance.pk:
        return
    if update_fields is not None and not set(update_fields) & set(FLAGS):
        return
    instance._audit_flags_before = (type(instance).objects.filter(pk=instance.pk)
                                    .values(*FLAGS).first())


def after_user_saved(sender, instance, created, **kwargs):
    if created:
        _record("account_created", instance, metadata={
            flag: bool(getattr(instance, flag)) for flag in FLAGS})
        return
    before = getattr(instance, "_audit_flags_before", None)
    instance._audit_flags_before = None
    if not before:
        return
    for flag, (on, off) in FLAGS.items():
        was, now = bool(before[flag]), bool(getattr(instance, flag))
        if was != now:
            _record(on if now else off, instance, before={flag: was}, after={flag: now})


def connect() -> None:
    user_model = get_user_model()
    user_logged_in.connect(on_login, weak=False, dispatch_uid="toto.audit.identity.login")
    user_logged_out.connect(on_logout, weak=False, dispatch_uid="toto.audit.identity.logout")
    user_login_failed.connect(on_login_failed, weak=False,
                              dispatch_uid="toto.audit.identity.login_failed")
    pre_save.connect(before_user_saved, sender=user_model, weak=False,
                     dispatch_uid="toto.audit.identity.user_before")
    post_save.connect(after_user_saved, sender=user_model, weak=False,
                      dispatch_uid="toto.audit.identity.user_after")
