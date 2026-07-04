"""
Helpers for checking whether the platform can actually deliver email.

Django always has an EMAIL_BACKEND, but the defaults used when no EMAIL_*
config is provided (console, dummy) silently discard mail. Flows that depend
on the user receiving an email — e.g. password reset — should be hidden when
delivery is not configured.
"""
from django.conf import settings

# Backends that silently discard mail. locmem is deliberately excluded so
# tests can exercise email flows.
_NON_DELIVERING_EMAIL_BACKENDS = (
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
)


def email_delivery_configured() -> bool:
    """True when Django's email backend can actually deliver mail to users."""
    return settings.EMAIL_BACKEND not in _NON_DELIVERING_EMAIL_BACKENDS
