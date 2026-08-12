"""What primula meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

A save rewrites the whole sheet file, so it is worth
counting — but note the seeded policy uses TRACK, not BLOCK, and carries no
price. Refusing a save loses the work in the user's browser, and with no
self-service top-up that is not recoverable without an admin. Counting it tells
us whether it ever needs a real cap; blocking it would be a rate card causing
data loss.
"""

from toto.quota.choices import Mode
from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="primula.save",
    label="Sheet save",
    app_label="primula",
    unit="request",
    description="One workbook save and the version snapshot it appends.",
    default_limit=500,
    metadata={"seed_mode": Mode.TRACK},
))
