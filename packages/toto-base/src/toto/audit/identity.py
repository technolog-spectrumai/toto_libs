"""Who came in, and who was let in: identity events on the chain (2026-09-28).

The chain recorded documents and money and said nothing about people coming
and going. Now it does, from Django's own signals — so every door is covered
at once: the login form, the SSO provider, axes' lockouts, the admin, the
membership flow, a management command.

| action | when |
|---|---|
| `AUTH.LOGIN` | a session starts (`user_logged_in`) |
| `AUTH.LOGOUT` | a session ends (`user_logged_out`) |
| `AUTH.LOGIN_FAILED` | credentials refused, a lockout included (`user_login_failed`); `success=False`, the attempted username only |
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
    from .services import SYSTEM

    _record("logout", user, actor_user=user if user is not None else SYSTEM, request=request)


def on_login_failed(sender, credentials, request=None, **kwargs):
    from .services import SYSTEM

    credentials = credentials or {}
    attempted = str(credentials.get("username") or credentials.get("email") or "")[:150]
    _record("login_failed", username=attempted, actor_user=SYSTEM, request=request,
            success=False, metadata={"username": attempted})


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
