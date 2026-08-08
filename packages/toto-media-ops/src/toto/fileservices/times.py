"""Fileservices' one time dial: how long one service run may run.

Imported from QuotaConfig.ready(), so this module stays pure data. The free
default is 1500 s — the honest current bound: the run task carries no celery
limit override of its own, so the global 1500 s soft limit kills a run long
before the plugins' nominal 7200 s subprocess timeout could. Raising the dial
routes the run onto a directly-limited task (see fileservices.dispatch).
"""

from toto.quota.times import TimeLimit, registry

registry.register(TimeLimit(
    key="fileservices.run_runtime",
    label="Service run runtime",
    app_label="fileservices",
    scope="user",
    free_seconds=1500,
    ceiling_seconds=7200,
    display_unit="minutes",
    description="How long one of your file-service runs may run before it is "
                "killed. Applies to runs you start after changing it.",
))
