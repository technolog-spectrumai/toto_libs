"""
Helpers for checking whether the platform can actually deliver email.

Django always has an EMAIL_BACKEND, but the defaults used when no EMAIL_*
config is provided (console, dummy) silently discard mail. Flows that depend
on the user receiving an email — e.g. password reset — should be hidden when
delivery is not configured.

Since ``toto.jess`` exists there are two ways to answer this, and which one applies
is decided by ``settings.EMAIL_BACKEND``:

* pointed at Jess     -> ask Jess, which knows whether a provider is configured,
                         whether it delivers, and whether its password is readable
* pointed anywhere else -> the original string comparison, unchanged

Dispatching on the backend string rather than on ``apps.is_installed("toto.jess")`` is
deliberate and load-bearing. Several suites do
``@override_settings(EMAIL_BACKEND=locmem)`` and then assert the reset link renders and
``mail.outbox`` fills — see ``sso_master/tests/test_password_reset.py`` and
``sso_core/federation/tests/test_local_accounts.py``. Under an override, Jess is not in
the send path at all, so it must not be in the answer path either: asking an
uninstalled-in-spirit Jess "can we deliver?" would hide the link those tests require and
break them for a reason that has nothing to do with what they test.
"""
from django.conf import settings

# Backends that silently discard mail. locmem is deliberately excluded so
# tests can exercise email flows.
_NON_DELIVERING_EMAIL_BACKENDS = (
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
)

# Jess's queueing backend. A literal rather than an import: toto.core must not import an
# optional app, and this module is evaluated on every login page render.
_JESS_BACKEND = "toto.jess.backend.JessEmailBackend"


def email_delivery_configured() -> bool:
    """True when Django's email backend can actually deliver mail to users."""
    if settings.EMAIL_BACKEND == _JESS_BACKEND:
        return _jess_delivery_configured()
    return settings.EMAIL_BACKEND not in _NON_DELIVERING_EMAIL_BACKENDS


def _jess_delivery_configured() -> bool:
    """Ask Jess. Soft edge, and it must never raise.

    Same shape as ``core.auth_views._social_login_providers`` — toto.core is installed
    everywhere and toto.jess is flag-gated, so the dependency can only go one way and
    only at call time.

    The bare ``except`` earns its keep here: this runs on BOTH login pages for anonymous
    users, and a host whose ``EMAIL_BACKEND`` already names Jess while its tables do not
    exist yet — mid-deploy, or during a ``collectstatic`` that loads settings — would
    otherwise raise ``ProgrammingError`` straight out of a login page.
    """
    from django.apps import apps

    if not apps.is_installed("toto.jess"):
        # A host pointing EMAIL_BACKEND at an app it does not install. Nothing will
        # send, so say so rather than letting the import below explain it.
        return False
    try:
        from toto.jess.status import can_deliver

        return can_deliver()
    except Exception:
        return False
