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
