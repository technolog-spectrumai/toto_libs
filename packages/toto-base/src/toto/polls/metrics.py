"""What polls meters: decision PDFs, and nothing else.

Voting itself is unmetered — charging to cast a ballot would price
participation. The export is different: a generated document on request.
Like every other PDF metric on the platform (memo.pdf) it is registered and
quota-capped but carries no PRICES entry:
metered, free by default, priceable by a host that wants to.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="polls.pdf",
    label=_("Decision PDF export"),
    app_label="polls",
    unit="request",
    default_limit=20,
    description=_("One PDF export of a vote's recorded decision or of the "
                  "decision ledger. Voting itself is free and unlimited."),
))
