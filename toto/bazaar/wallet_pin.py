import hmac
import hashlib
import time

from django.conf import settings

_STRONGBOX_NAME = "bazaar-wallet-pin"
_PIN_SECRET_NAME = "wallet-pin"
_SESSION_KEY_OK = "wallet_pin_ok"
_SESSION_KEY_EXP = "wallet_pin_exp"
_SESSION_TTL = 300  # seconds


def _vault_password(user) -> str:
    secret = getattr(settings, "WALLET_VAULT_SECRET", settings.SECRET_KEY)
    return hmac.new(secret.encode(), str(user.pk).encode(), hashlib.sha256).hexdigest()


def has_wallet_pin(user) -> bool:
    from toto.bazaar.models import WalletPin
    return WalletPin.objects.filter(user=user).exists()


def set_wallet_pin(user, raw_pin: str) -> None:
    from toto.bazaar.models import WalletPin
    from toto.gervazy.models import UserStrongbox
    from toto.gervazy.crypto import GervazyCryptoSession

    password = _vault_password(user)

    # Wipe any previous WalletPin + strongbox entirely so there are no
    # stale keys or EncryptedSecrets that would cause InvalidTag or
    # UniqueConstraint errors on re-use.
    WalletPin.objects.filter(user=user).delete()
    UserStrongbox.objects.filter(owner=user, name=_STRONGBOX_NAME).delete()

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
