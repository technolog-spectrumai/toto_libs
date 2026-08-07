"""What fileservices meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

This is what the EXPENSIVE tier was defined for and never used on: an ffmpeg job
can hold the single shared celery worker for hours. Note that no zenobia profile
installs this app today, so the metric registers and never fires — which is the
point of declaring it now. The cap is already in place on the day someone sets
the flag, rather than being remembered afterwards.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="fileservices.run",
    label="File service run",
    app_label="fileservices",
    unit="request",
    description="One ffmpeg-tier service run turning one vault file into another.",
    default_limit=40,
))
