"""What sketch meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

A save rewrites the whole drawing, so it is worth counting. The seeded policy
uses TRACK, not BLOCK, for the reason primula states: refusing a save loses work
that exists only in a browser tab, and with no self-service top-up that is not
recoverable without an admin. Counting it tells us whether it ever needs a real
cap; blocking it would be a rate card that causes data loss. Affordability is
decided at the door instead — see toto/vault/editing.py.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.choices import Mode
from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="sketch.save",
    label=_("Drawing save"),
    app_label="sketch",
    unit="request",
    description="One drawing written back to its file, and the version it keeps.",
    default_limit=500,
    metadata={"seed_mode": Mode.TRACK},
))
