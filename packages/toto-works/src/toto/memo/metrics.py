"""What memo meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

Exporting a deck runs WeasyPrint **synchronously in the request**, one page per
slide, so a large deck holds a web worker for the length of the render. The cap
is generous on purpose: exporting your own deck is the app working as intended,
and a refusal in the middle of preparing a talk is a support ticket, not a
defence. It is here to stop a loop, not to ration the feature.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="memo.pdf",
    label="Deck export",
    app_label="memo",
    unit="request",
    description="One WeasyPrint render of a presentation, one page per slide.",
    default_limit=50,
))
