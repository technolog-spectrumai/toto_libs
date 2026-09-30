"""A token is a session key, and it is checked like one (2026-09-30).

The desktop clients sign in at ``/api/login/`` (or ``/sso/api/register/``) and
keep the session key they are handed. They present it as
``Authorization: Bearer <key>`` to every ``CorsApiView`` JSON door
(``cors._try_bearer_auth``) and as ``?token=<key>`` to a WebSocket
(``middleware.TokenAuthMiddleware``). Both doors resolve it HERE, and nowhere
else.

Until this date both doors read ``_auth_user_id`` out of the session and
fetched that row with a bare ``User.objects.get``. That skipped the two checks
Django's own ``django.contrib.auth.get_user`` makes on every cookie request:
the backend's ``get_user`` (``ModelBackend`` refuses an inactive account there,
through ``user_can_authenticate``) and the session hash (a password change
invalidates every session signed with the old one). So a deactivated account
and a changed password kept working until the session expired — fourteen days
by default. This module makes those two checks, in the same order and with
the same constant-time comparison.

Where it deliberately differs from ``get_user``:

- A refused sign-in is FLUSHED whatever the reason — a deactivated or deleted
  account as well as a stale hash. Django leaves an inactive account's session
  in place; a token that has been refused once should stay refused (the
  account signs in again once it is re-activated), and a client that keeps
  polling with it must not write a refusal onto the chain every few seconds.
- A hash that matches only one of ``SECRET_KEY_FALLBACKS`` is re-signed IN
  PLACE. Django cycles the session key there, which is right for a cookie the
  browser is sent again and wrong for a token: the client would never learn
  its new key, and every desktop would be signed out by a key rotation.
- The store is ``SESSION_ENGINE``'s, as ``SessionMiddleware`` uses, not the
  database backend's by name.

A refused token is answered exactly as no token would be: the caller stays
anonymous and learns nothing about why. The reason goes on the audit chain
(``AUTH.TOKEN_REFUSED``, ``toto.audit.identity``) — never the key itself, and
only when the key named an account: an unknown or expired key is nothing
worth recording, and recording it would let anyone fill the chain.
"""

from __future__ import annotations

import logging
from importlib import import_module

log = logging.getLogger("toto.api")

#: The doors a token is presented at, as the chain names them.
DOOR_API = "api"
DOOR_WEBSOCKET = "websocket"


def _session_store(session_key):
    from django.conf import settings

    return import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)


def user_for_session_key(session_key, *, door, request=None):
    """The account a session key signs in, or ``None``.

    ``None`` for anything that is not a live, verified sign-in: an unknown or
    expired key, a session that signed nobody in, a backend no longer listed,
    an account the backend refuses (inactive) or that is gone, and a session
    whose hash no longer matches the account's password. ``request`` is the
    HTTP request the key came with, for the chain's record of a refusal; a
    socket has none.
    """
    from django.conf import settings
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
        load_backend,
    )
    from django.utils.crypto import constant_time_compare

    if not session_key or not isinstance(session_key, str):
        return None
    try:
        session = _session_store(session_key)
        raw_user_id = session.get(SESSION_KEY)
        backend_path = session.get(BACKEND_SESSION_KEY)
    except Exception as exc:  # noqa: BLE001 - a store that cannot be read signs nobody in
        # The key is a credential: it is never logged, not even in part — and
        # neither is the exception's text, which a cache-backed store can
        # write the key into. Its class is enough to go and look.
        log.warning("api token: the session store could not be read (%s)",
                    type(exc).__name__)
        return None
    if raw_user_id is None:
        return None

    try:
        user_id = get_user_model()._meta.pk.to_python(raw_user_id)
        if backend_path not in settings.AUTHENTICATION_BACKENDS:
            # The sign-in was made through a backend this host no longer
            # trusts (or through none: a session written by hand).
            return _refuse(session, user_id, "backend", door=door, request=request)
        user = load_backend(backend_path).get_user(user_id)
        if user is None:
            return _refuse(session, user_id, None, door=door, request=request)
        if not hasattr(user, "get_session_auth_hash"):
            return user
        stored = session.get(HASH_SESSION_KEY)
        current = user.get_session_auth_hash()
        if stored and constant_time_compare(stored, current):
            return user
        fallbacks = getattr(user, "get_session_auth_fallback_hash", lambda: ())()
        if stored and any(constant_time_compare(stored, old) for old in fallbacks):
            session[HASH_SESSION_KEY] = current
            session.save()
            return user
        return _refuse(session, user_id, "session_hash", door=door, request=request,
                       user=user)
    except Exception as exc:  # noqa: BLE001 - never flush a session on a passing fault
        # A database hiccup is not a refusal: the session is kept, so the next
        # request can succeed, and nothing is recorded.
        log.warning("api token: the sign-in could not be checked (%s)",
                    type(exc).__name__)
        return None


def _refuse(session, user_id, reason, *, door, request, user=None):
    """Flush the session, put the refusal on the chain, answer ``None``.

    ``reason`` ``None`` means the backend's ``get_user`` said no; the row
    itself then says whether the account is gone, inactive, or refused by the
    backend for a reason of its own.
    """
    from django.apps import apps
    from django.contrib.auth import get_user_model

    if user is None:
        user = get_user_model()._default_manager.filter(pk=user_id).first()
    if reason is None:
        if user is None:
            reason = "no_account"
        elif not getattr(user, "is_active", True):
            reason = "inactive"
        else:
            reason = "backend_refused"
    session.flush()
    if apps.is_installed("toto.audit"):
        from toto.audit.identity import on_token_refused

        on_token_refused(user, account_id=user_id, reason=reason, door=door,
                         request=request)
    return None
