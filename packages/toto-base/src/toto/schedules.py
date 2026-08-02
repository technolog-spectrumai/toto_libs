"""Celery beat schedule entries owned by toto apps.

celery is imported lazily inside the function: the basic WSGI tier ships
without celery, and hosts only call this with a feature enabled when the
celery stack is installed (exactly the pre-split arrangement).
"""


def beat_schedule(
    *,
    weather=False,
    weather_minutes=30,
    connectors=False,
    connectors_minutes=1,
    formica=False,
    formica_minutes=5,
    monit=False,
    monit_minutes=2,
    clearing=False,
):
    """Build the CELERY_BEAT_SCHEDULE dict for the enabled features."""
    schedule = {}

    if weather:
        from celery.schedules import crontab

        schedule["auto-refresh-weather"] = {
            "task": "toto.weather.tasks.auto_refresh_weather",
            "schedule": crontab(minute=f"*/{weather_minutes}"),
        }

    if connectors:
        from celery.schedules import crontab

        # A single global scan task, weather-style — no per-object beat entry.
        schedule["connectors-scan-schedules"] = {
            "task": "toto.connectors.tasks.connectors_scan_schedules",
            "schedule": crontab(minute=f"*/{connectors_minutes}")
            if connectors_minutes > 1
            else crontab(),
        }

    if formica:
        from celery.schedules import crontab

        # Each colony has its own interval_minutes — this is just the scan cadence.
        schedule["formica-beat-scan"] = {
            "task": "toto.formica.tasks.formica_beat_scan",
            "schedule": crontab(minute=f"*/{formica_minutes}"),
        }

    if monit:
        from celery.schedules import crontab

        # Snapshot sampler for the toto.monit dashboard (faros-only app);
        # history is pruned hourly to MONIT_RETENTION_HOURS.
        schedule["monit-sample"] = {
            "task": "toto.monit.tasks.monit_sample",
            "schedule": crontab(minute=f"*/{monit_minutes}")
            if monit_minutes > 1
            else crontab(),
        }
        schedule["monit-prune"] = {
            "task": "toto.monit.tasks.monit_prune",
            "schedule": crontab(minute="17"),
        }

    if clearing:
        from celery.schedules import crontab

        # Delivery is attempted inline on commit; this is the safety net that
        # picks up whatever a crash or an unreachable peer left behind.
        schedule["clearing-dispatch-outbox"] = {
            "task": "toto.clearing.tasks.dispatch_outbox",
            "schedule": crontab(),
        }
        # Re-queue failed sends below the attempt cap. Deliberately slower than
        # dispatch: a peer that is down stays down for more than a minute, and
        # hammering it is not a retry strategy.
        schedule["clearing-redrive"] = {
            "task": "toto.clearing.tasks.redrive_failed",
            "schedule": crontab(minute="*/15"),
        }
        # Expiry frees escrowed value whose remote outcome never arrived.
        schedule["clearing-expire-holds"] = {
            "task": "toto.clearing.tasks.expire_holds",
            "schedule": crontab(minute="*/5"),
        }

    return schedule
