"""What manta meters.

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
    code="manta.job",
    label="ffmpeg job",
    app_label="manta",
    unit="request",
    description="One ffmpeg command run over vault files — minutes of CPU, and the only tasks on this platform with a two-hour time limit rather than thirty minutes.",
    default_limit=40,
))
