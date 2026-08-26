"""What cyprian meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

One metric: a save. It is counted and priced, and the seeded policy is TRACK
rather than BLOCK, carrying a limit that is a tripwire rather than a ration.
Refusing a save loses the work in the user's browser, and with no self-service
top-up that is not recoverable without an admin — so affordability is decided at
the door instead, when nothing is at stake. toto/vault/editing.py states the
whole rule.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.choices import Mode
from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="cyprian.save",
    label=_("Document save"),
    app_label="cyprian",
    unit="request",
    description="One document written back to its file, and the version it keeps.",
    default_limit=500,
    metadata={"seed_mode": Mode.TRACK},
))
