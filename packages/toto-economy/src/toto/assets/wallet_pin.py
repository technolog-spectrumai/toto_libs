import hmac
import hashlib
import secrets
import time
import threading

from django.conf import settings

_STRONGBOX_NAME = "assets-wallet-pin"
_PIN_SECRET_NAME = "wallet-pin"
_SESSION_KEY_OK = "wallet_pin_ok"
_SESSION_KEY_EXP = "wallet_pin_exp"
_SESSION_TTL = 300  # seconds

# ── Bearer-token PIN verification store ──────────────────────────────────────
# Maps opaque token → (user_id, expiry). Lives in-process; expires in 5 min.
_PIN_TOKENS: dict[str, tuple[int, float]] = {}
_PIN_TOKENS_LOCK = threading.Lock()


def issue_pin_token(user) -> str:
    """Return a single-use-style opaque token valid for _SESSION_TTL seconds."""
    token = secrets.token_urlsafe(32)
    exp = time.time() + _SESSION_TTL
    with _PIN_TOKENS_LOCK:
        # Evict expired entries opportunistically.
        now = time.time()
        expired = [k for k, (_, e) in _PIN_TOKENS.items() if e < now]
        for k in expired:
            del _PIN_TOKENS[k]
        _PIN_TOKENS[token] = (user.pk, exp)
    return token


def verify_pin_token(token: str, user) -> bool:
    """Return True if token is valid and belongs to this user."""
    with _PIN_TOKENS_LOCK:
        entry = _PIN_TOKENS.get(token)
    if not entry:
        return False
    uid, exp = entry
    return uid == user.pk and time.time() < exp


def _vault_password(user) -> str:
    secret = getattr(settings, "WALLET_VAULT_SECRET", settings.SECRET_KEY)
    return hmac.new(secret.encode(), str(user.pk).encode(), hashlib.sha256).hexdigest()


def has_wallet_pin(user) -> bool:
    from toto.assets.models import WalletPin
    return WalletPin.objects.filter(user=user).exists()


def set_wallet_pin(user, raw_pin: str) -> None:
    from toto.assets.models import WalletPin
    from toto.gervazy.models import (
        EncryptedFile,
        EncryptedPrivateKey,
        EncryptedSecret,
        UserStrongbox,
        VaultMasterKey,
        WrappedDataKey,
    )
    from toto.gervazy.crypto import GervazyCryptoSession
    from django.db import transaction

    password = _vault_password(user)

    # Wipe the dedicated wallet-PIN strongbox. Gervazy protects parent key
    # rows, so delete children first instead of relying on a parent cascade.
    with transaction.atomic():
        WalletPin.objects.filter(user=user).delete()
        strongboxes = UserStrongbox.objects.filter(owner=user, name=_STRONGBOX_NAME)
        for strongbox in strongboxes:
            EncryptedSecret.objects.filter(strongbox=strongbox).delete()
            EncryptedFile.objects.filter(strongbox=strongbox).delete()
            EncryptedPrivateKey.objects.filter(strongbox=strongbox).delete()
            WrappedDataKey.objects.filter(strongbox=strongbox).delete()
            VaultMasterKey.objects.filter(strongbox=strongbox).delete()
            strongbox.delete()

        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            user, _STRONGBOX_NAME, password
        )

        secret = session.encrypt_secret(
            wrapped_key,
            raw_pin,
            name=_PIN_SECRET_NAME,
            purpose="wallet_pin",
        )
        WalletPin.objects.create(user=user, secret=secret)


def check_wallet_pin(user, raw_pin: str) -> bool:
    from toto.assets.models import WalletPin
    from toto.gervazy.crypto import GervazyCryptoSession

    try:
        wp = WalletPin.objects.select_related("secret__strongbox").get(user=user)
    except WalletPin.DoesNotExist:
        return True  # No PIN set → always pass.

    password = _vault_password(user)
    try:
        session = GervazyCryptoSession(wp.secret.strongbox, password)
        stored = session.decrypt_secret(wp.secret)
        return stored == raw_pin
    except Exception as exc:
        import traceback, logging
        logging.getLogger(__name__).error("check_wallet_pin failed: %s", traceback.format_exc())
        return False


# ── Attempt limit (stage 51) ──────────────────────────────────────────────────
# A check used to count nothing: 10^4 tries open a four-digit PIN, and a hit
# marks the session verified for five minutes, which is what a bourse accept
# takes in place of a pin_token. Now, per member and in the shared cache
# (toto.core.ratelimit, as the sign-in lockout counts), WALLET_PIN_LOCK_AFTER
# wrong PINs lock both doors for WALLET_PIN_LOCK_SECONDS, doubling with each
# further lock up to WALLET_PIN_LOCK_MAX_SECONDS. While locked even the right
# PIN is refused, and the lock ends the session mark and the member's tokens.
# A try is counted before the PIN is compared, so tries sent together cannot
# all slip in before the first failure is counted. Fail open, like the
# limiter: a cache that cannot answer counts nothing.

PIN_LOCK_DEFAULTS = {
    "WALLET_PIN_LOCK_AFTER": 5,
    "WALLET_PIN_LOCK_SECONDS": 60,
    "WALLET_PIN_LOCK_MAX_SECONDS": 86400,
}
_LOCKS_WINDOW = 7 * 86400     # the growth is forgotten a week after the last lock
_TRIES_WINDOW = 86400         # wrong PINs are forgotten a day after the last one


def _lock_setting(name: str) -> int:
    try:
        return max(1, int(getattr(settings, name, PIN_LOCK_DEFAULTS[name])))
    except (TypeError, ValueError):
        return PIN_LOCK_DEFAULTS[name]


def _keys(user) -> tuple[str, str, str]:
    """(the tries of this lock round, the round counter, the lock)."""
    from toto.core import ratelimit

    base = f"wallet-pin:{user.pk}"
    rounds = ratelimit.peek(f"{base}:rounds")
    return f"{base}:tries:{rounds}", f"{base}:rounds", f"{base}:lock"


def _drop_tokens(user) -> None:
    with _PIN_TOKENS_LOCK:
        for token in [k for k, (uid, _) in _PIN_TOKENS.items() if uid == user.pk]:
            del _PIN_TOKENS[token]


def _lock(user, tries_key: str, rounds_key: str, lock_key: str) -> None:
    """Lock once per round, for longer each round."""
    from toto.core import ratelimit

    if not ratelimit.hold(f"{tries_key}:locked", seconds=_TRIES_WINDOW):
        return                                    # this round is locked already
    rounds = ratelimit.count(rounds_key, window=_LOCKS_WINDOW) or 1
    seconds = min(_lock_setting("WALLET_PIN_LOCK_SECONDS") * 2 ** min(rounds - 1, 30),
                  _lock_setting("WALLET_PIN_LOCK_MAX_SECONDS"))
    ratelimit.hold(lock_key, seconds=seconds, replace=True)
    _drop_tokens(user)


def pin_locked_for(user) -> int:
    """Seconds left on this member's PIN lock, 0 when there is none."""
    import math

    from toto.core import ratelimit

    until = ratelimit.held_until(_keys(user)[2])
    return max(1, math.ceil(until - time.time())) if until else 0


def attempt_wallet_pin(user, raw_pin: str, session=None) -> tuple[bool, int]:
    """Check a PIN under the attempt limit: (right, seconds locked).

    Seconds locked is non-zero when the PIN was not compared at all because
    the member's PINs are locked; the session mark is cleared then.
    """
    from toto.core import ratelimit

    tries_key, rounds_key, lock_key = _keys(user)
    locked = pin_locked_for(user)
    if not locked:
        tries = ratelimit.count(tries_key, window=_TRIES_WINDOW)
        if tries is not None and tries > _lock_setting("WALLET_PIN_LOCK_AFTER"):
            _lock(user, tries_key, rounds_key, lock_key)  # sent past the limit together
            locked = pin_locked_for(user) or 1
    if locked:
        if session is not None:
            clear_session(session)
        return False, locked

    if check_wallet_pin(user, raw_pin):
        ratelimit.forget(tries_key)
        return True, 0
    if (ratelimit.peek(tries_key) >= _lock_setting("WALLET_PIN_LOCK_AFTER")):
        _lock(user, tries_key, rounds_key, lock_key)
        if session is not None:
            clear_session(session)
    return False, 0


def pin_locked_response(seconds: int):
    """The doors' one answer while the PINs are locked: 429 with Retry-After."""
    from django.http import JsonResponse

    response = JsonResponse({"ok": False, "locked": True, "retry_after": seconds,
                             "error": "Too many wrong PINs. Try again later."}, status=429)
    response["Retry-After"] = str(seconds)
    return response


def mark_session_verified(session) -> None:
    session[_SESSION_KEY_OK] = True
    session[_SESSION_KEY_EXP] = time.time() + _SESSION_TTL


def session_is_verified(session) -> bool:
    return (
        session.get(_SESSION_KEY_OK)
        and time.time() < session.get(_SESSION_KEY_EXP, 0)
    )


def clear_session(session) -> None:
    session.pop(_SESSION_KEY_OK, None)
    session.pop(_SESSION_KEY_EXP, None)
