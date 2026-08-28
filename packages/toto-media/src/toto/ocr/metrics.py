"""What OCR meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

A page is the unit because a page is the work: one rasterisation and one read,
a few seconds each. Charging per submission would price a screenshot and a
300-page book the same.

500/day is a rate limit on submissions rather than a price on compute, the way
anastasia.execution's 200 is. Asynchronous work is not self-limiting — nobody
waits for it — and this platform runs every background job on one queue. The
real controls are the admin's page cap and one-run-at-a-time; this is the
backstop behind them.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="ocr.page",
    label=_("Page read"),
    app_label="ocr",
    unit="page",
    description=_(
        "One page of an image or a scanned PDF read by Tesseract. Counted when "
        "the page is delivered, so a page that fails costs nothing."
    ),
    default_limit=500,
))
