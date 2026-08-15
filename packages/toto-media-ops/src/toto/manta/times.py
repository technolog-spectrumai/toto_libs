"""Manta's one time dial: how long one of a user's ffmpeg jobs may run.

Imported from QuotaConfig.ready(), so this module stays pure data. The
ceiling is bound to the host's Redis visibility timeout: the celery hard
limit becomes ceiling + 100, and the broker must not redeliver a task that is
still legitimately running — zenobia's settings raise visibility_timeout in
lockstep and a settings test guards the pair. Dispatch additionally clamps.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.times import TimeLimit, registry

registry.register(TimeLimit(
    key="manta.job_runtime",
    label=_("Media job runtime"),
    app_label="manta",
    scope="user",
    free_seconds=7200,
    ceiling_seconds=14400,
    display_unit="hours",
    description=_("How long one of your ffmpeg jobs may run before it is "
                  "killed. Applies to jobs you start after changing it."),
))
