"""What the antivirus meters: on-demand scans, and nothing else.

Door screening is deliberately unmetered — it is the platform protecting
itself, and charging for it would price honesty. The Scan button is different:
ordered work on a worker, one code, one price.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="antivirus.scan",
    label=_("On-demand scan"),
    app_label="antivirus",
    unit="scan",
    default_limit=500,
    description=_("One file scanned on request from the antivirus app. "
                  "Automatic screening at the doors is free and unlimited."),
))
