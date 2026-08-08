"""What tax itself meters.

Imported from QuotaConfig.ready(), so this module stays pure data — and it is
only imported on hosts that install toto.tax, which is exactly where the
resource exists. The metric names the resource (extended time held), not the
app, mirroring storage.gb_day.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="time.hold",
    label="Extended time held",
    app_label="tax",
    unit="hour_day",
    description="Hours of time-limit extension held above the free defaults "
                "(demurrage), charged per day whether used or not. The dials "
                "live on /tax/demurrage/; the price on the rate card.",
))
