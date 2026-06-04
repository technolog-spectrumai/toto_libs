import hmac
import hashlib
import secrets
import time
import threading

from django.conf import settings

_STRONGBOX_NAME = "bazaar-wallet-pin"
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
    from toto.bazaar.models import WalletPin
    return WalletPin.objects.filter(user=user).exists()


def set_wallet_pin(user, raw_pin: str) -> None:
    from toto.bazaar.models import WalletPin
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
    from toto.bazaar.models import WalletPin
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
