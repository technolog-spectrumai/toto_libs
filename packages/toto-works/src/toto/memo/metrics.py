"""What memo meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

Two metrics that ration nothing in the same way. The export is capped to stop a
loop; the save is counted and priced but seeded TRACK, because refusing a save
destroys work that exists only in a browser tab. See toto/vault/editing.py.

Exporting a deck runs WeasyPrint **synchronously in the request**, one page per
slide, so a large deck holds a web worker for the length of the render. The cap
is generous on purpose: exporting your own deck is the app working as intended,
and a refusal in the middle of preparing a talk is a support ticket, not a
defence. It is here to stop a loop, not to ration the feature.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.choices import Mode
from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="memo.pdf",
    label=_("Deck export"),
    app_label="memo",
    unit="request",
    description="One WeasyPrint render of a presentation, one page per slide.",
    default_limit=50,
))

registry.register(Metric(
    code="memo.save",
    label=_("Deck save"),
    app_label="memo",
    unit="request",
    description="One deck written back to its file, and the version it keeps.",
    default_limit=500,
    # TRACK, not BLOCK, and the limit is a tripwire rather than a ration: a
    # refused save is lost work, and this platform has no self-service top-up.
    metadata={"seed_mode": Mode.TRACK},
))
