"""What vault meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="storage.request",
    label="File upload",
    app_label="vault",
    unit="request",
    description="One file accepted through a gateway or the file API.",
    default_limit=500,
))

registry.register(Metric(
    code="storage.transfer_mb",
    label="Bytes transferred",
    app_label="vault",
    unit="mb",
    description="Megabytes moved by an upload. Charged by size, not per call.",
    default_limit=2000,
))

# Levied by the clock, not the click: toto.tax samples each owner's stored
# bytes once a day and charges the part above the free allowance. No
# default_limit — a cap on holdings already billed by the day is meaningless,
# and its absence keeps ingress_quota from seeding a policy row.
registry.register(Metric(
    code="storage.gb_day",
    label="Storage held",
    app_label="vault",
    unit="gb_day",
    description="Gigabytes stored above the free allowance, sampled nightly. "
                "The allowance lives in toto.tax; the price on the rate card.",
))
