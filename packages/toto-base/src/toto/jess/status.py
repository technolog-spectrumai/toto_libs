"""Can this platform actually deliver mail? One honest answer, for one caller.

``toto.core.email_config.email_delivery_configured()`` decides whether the "Forgot
password?" link renders and whether ``/sso/password-reset/`` is reachable at all. Before
Jess it answered by string-comparing ``settings.EMAIL_BACKEND`` against console/dummy —
which says nothing about whether a host is reachable, a password is readable, or a
provider even exists.

That mattered because studio has local accounts whose only recovery path is that link.
Advertising a reset that cannot be delivered is worse than hiding it: the user believes
mail is coming.
"""
from __future__ import annotations

from . import vault
from .models import EmailProvider


def can_queue() -> bool:
    """Could a message queued now eventually reach a person? **Configuration only.**

    Never opens a session and never reads a secret, so it is safe on an anonymous login
    render even in manual-release mode, where there is no ambient passphrase to read one
    with. This is the weaker question ``can_deliver`` falls back to under manual release:
    a queued message becomes a HELD demand that an admin can later release.
    """
    try:
        provider = EmailProvider.active_provider()
        if provider is None or not provider.delivers:
            return False
        if provider.needs_secret and not provider.secret_id:
            return False
        return True
    except Exception:
        return False


def can_send_inline() -> bool:
    """Could a reset email be sent RIGHT NOW, inline, on an in-memory credential?

    The third custody mode (``credentials.py``): the active provider has a
    username but no stored secret, and this web process holds the password a
    staff member typed (or the env bootstrap). ``can_queue``/``can_deliver``
    both answer False for such a provider — nothing stored means nothing a
    Celery worker could ever decrypt — so the password-reset flow asks this
    beside them and, when it says yes, sends in the request itself.

    Never raises: it is called while rendering an anonymous login page, on a
    host whose tables may not exist yet.
    """
    try:
        provider = EmailProvider.active_provider()
        if provider is None or not provider.delivers:
            return False
        if not provider.needs_secret:
            # An open relay needs no credential; can_deliver already says True
            # and the ordinary queue path handles it.
            return False
        if provider.secret_id:
            # A stored secret means the ordinary custody modes apply.
            return False
        from . import credentials

        return credentials.available()
    except Exception:
        return False


def can_deliver() -> bool:
    """True only when a message queued right now could actually reach a person.

    Deliberately strict, and deliberately never raising — it is called while rendering a
    login page.

    **In manual-release mode this returns ``can_queue()``**: there is no passphrase on
    the server to prove the secret decrypts, and a reset requested now legitimately
    queues as a HELD demand for an admin to release — so the login page keeps offering
    "Forgot password?". The stricter secret-reading check below is the non-manual path.

    | state                                   | result | why                                     |
    |-----------------------------------------|--------|-----------------------------------------|
    | no active provider                      | False  | nothing to send through                 |
    | active, backend console/dummy           | False  | matches the pre-Jess semantics exactly  |
    | active smtp, no password needed         | True   | an open relay or IP-authenticated host  |
    | active smtp, password unreadable        | False  | the vault is the thing that is broken   |
    | active smtp, password readable          | True   | as far as configuration can tell        |

    What it does NOT do is open a socket. A reachability probe on a login page render
    would put a third party's latency on the critical path of every anonymous request —
    the same reason the codebase rejected ``celery_available()`` pings on hot paths. So
    "could" here means "is configured to", and an unreachable server still surfaces as a
    failed MailMessage with the error kept verbatim.
    """
    if vault.manual_release_enabled():
        return can_queue()
    try:
        provider = EmailProvider.active_provider()
        if provider is None or not provider.delivers:
            return False
        if not provider.needs_secret:
            return True
        if not provider.secret_id:
            # A username with no stored password will fail SMTP AUTH.
            return False
        # create=False: this runs on an anonymous login page — see vault.open_session.
        vault.read_secret(provider.secret, create=False)
        return True
    except Exception:
        # VaultUnavailable, a missing strongbox, a database that is not migrated yet
        # (this can be reached during a boot smoke before migrate) — all mean "no".
        return False


def describe() -> str:
    """A one-line diagnosis for the staff pages and the admin. Never raises."""
    try:
        provider = EmailProvider.active_provider()
        if provider is None:
            return "No active email provider — nothing can be sent."
        if not provider.delivers:
            return (
                f"'{provider.label}' is active but its backend "
                f"({provider.get_backend_display()}) does not deliver."
            )
        if provider.needs_secret and not provider.secret_id:
            # Session custody: no stored password is a mode, not only a gap.
            from . import credentials

            if credentials.available():
                return (
                    f"Session custody — sending through '{provider.label}' on a "
                    "credential held in this process's memory."
                )
            return (
                f"'{provider.label}' has a username but no stored password. "
                "Unlock one per session (Jess → Unlock), or resets fall back "
                "to patron-approved recovery."
            )
        if vault.manual_release_enabled():
            # Never read the secret here — there is no ambient passphrase. Report the
            # custody state and the backlog an admin has to release.
            from .models import MailMessage

            held = MailMessage.objects.filter(status=MailMessage.HELD).count()
            return (
                f"Manual release — sending through '{provider.label}'. "
                f"{held} message(s) held; an admin must type the passphrase to send."
            )
        if provider.needs_secret:
            try:
                vault.read_secret(provider.secret, create=False)
            except Exception as exc:
                return f"'{provider.label}': {exc}"
        return f"Sending through '{provider.label}'."
    except Exception as exc:                                # pragma: no cover
        return f"Email status unavailable ({type(exc).__name__})."
