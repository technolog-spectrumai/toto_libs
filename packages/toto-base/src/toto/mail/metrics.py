"""What Mail meters: one send, per recipient row.

Metered here rather than in a mail backend for the reason jess records at
``jess/metrics.py:1-25`` — a backend has no request and no user, and metering
anonymous platform mail would bill the wrong person for a password reset
somebody else asked for. Only a person pressing Send in this app is charged,
and platform mail through the system mailbox is not metered at all.
"""
from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="mail.send",
    label=_("Message sent"),
    app_label="mail",
    unit="message",
    description=_("One message sent from your own mailbox. Platform mail "
                  "sent by the system mailbox is not metered."),
    default_limit=200,
))
