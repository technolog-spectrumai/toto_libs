"""What cyprian meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

Same shape and the same reasoning as memo.pdf: WeasyPrint runs synchronously in
the request, so the cap exists to stop a loop rather than to ration exporting
your own document.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="cyprian.pdf",
    label="Document export",
    app_label="cyprian",
    unit="request",
    description="One WeasyPrint render of a document, with contents and page numbers.",
    default_limit=50,
))
