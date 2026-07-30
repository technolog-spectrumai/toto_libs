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


def can_deliver() -> bool:
    """True only when a message queued right now could actually reach a person.

    Deliberately strict, and deliberately never raising — it is called while rendering a
    login page.

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
            return f"'{provider.label}' has a username but no stored password."
        if provider.needs_secret:
            try:
                vault.read_secret(provider.secret, create=False)
            except Exception as exc:
                return f"'{provider.label}': {exc}"
        return f"Sending through '{provider.label}'."
    except Exception as exc:                                # pragma: no cover
        return f"Email status unavailable ({type(exc).__name__})."
