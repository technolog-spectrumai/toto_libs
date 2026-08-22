"""What polls meters: generated PDFs, and nothing else.

Answering is unmetered — charging to respond to a consultation would price
participation. A generated document is different: it is work done on request.

The metric was ``Decision PDF export`` until 1.50, when formal votes and their
decision ledger left for Irena. The CODE is unchanged on purpose: it is the
key a deployed host's quota rows and any priced rate card are already keyed
on, and renaming it would silently reset every limit somebody had set. What it
counts today is the quiz certificate.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="polls.pdf",
    label=_("Polls PDF export"),
    app_label="polls",
    unit="request",
    default_limit=20,
    description=_("One generated PDF — today, a quiz certificate. Answering "
                  "a consultation is free and unlimited."),
))
