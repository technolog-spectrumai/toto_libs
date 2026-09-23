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
    gitea=False,
    gitea_hour=3,
    gitea_minute=30,
    forum_cleanup=False,
    forum_cleanup_hour=4,
    forum_cleanup_minute=40,
    ocr_cleanup=False,
    ocr_cleanup_hour=4,
    ocr_cleanup_minute=50,
    clearing=False,
    tax=False,
    tax_hour=4,
    tax_minute=15,
    faucets=False,
    faucets_minute=7,
    mana=False,
    mana_minute=13,
    subscriptions=False,
    subscriptions_hour=5,
    subscriptions_minute=5,
    sweep=False,
    anastasia=False,
    anastasia_minutes=2,
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

    if gitea:
        from celery.schedules import crontab

        # The hosted-git storage sample + cap reconciler. Before the tax
        # sweep on purpose: the levy reads the snapshot this writes, and an
        # hour-old sample beats a day-old one. Correctness never depends on
        # the ordering — only staleness does.
        schedule["gitea-sample-storage"] = {
            "task": "toto.gitea.tasks.gitea_sample_storage",
            "schedule": crontab(hour=gitea_hour, minute=gitea_minute),
        }

    if forum_cleanup:
        from celery.schedules import crontab

        # Nightly, and harmless until somebody turns retention on: the task
        # reads ForumRetentionPolicy.enabled first and returns without touching
        # a row while it is False. Scheduling it from the start means the dial
        # is the ONE switch — there is no second, deploy-time flag that can
        # disagree with what the page says.
        schedule["forum-cleanup"] = {
            "task": "toto.forum.tasks.forum_cleanup",
            "schedule": crontab(hour=forum_cleanup_hour,
                                minute=forum_cleanup_minute),
        }

    # 04:50, behind the tax levy and the forum sweep — three long jobs on one
    # queue should not start together. This removes finished runs and the
    # SCANS THEMSELVES: an OCR upload can be 64 MB, so a host that never swept
    # would fill its disk with books nobody is reading any more.
    if ocr_cleanup:
        from celery.schedules import crontab

        schedule["ocr-cleanup"] = {
            "task": "toto.ocr.tasks.ocr_cleanup",
            "schedule": crontab(hour=ocr_cleanup_hour,
                                minute=ocr_cleanup_minute),
        }

    if tax:
        from celery.schedules import crontab

        # One sweep a day: sample resource holdings, levy the part above each
        # rule's free allowance. Idempotent per user/day via the usage-event
        # key, so a double fire is free; a missed day is never backfilled.
        schedule["tax-daily-levy"] = {
            "task": "toto.tax.tasks.run_daily_levy",
            "schedule": crontab(hour=tax_hour, minute=tax_minute),
        }

    if mana:
        from celery.schedules import crontab

        # The mana pools' hourly refill (toto.mana), off the faucet's :07 and
        # the hour boundary. Idempotent per member, pool and hour
        # (mana.ManaGrant), and it never backfills a missed hour.
        schedule["mana-hourly-regen"] = {
            "task": "toto.mana.tasks.regenerate_hour",
            "schedule": crontab(minute=str(mana_minute)),
        }

    if faucets:
        from celery.schedules import crontab

        # Once an hour, and the step is not configurable: every faucet amount is
        # denominated per hour, so a run that covered anything else would be
        # paying a rate nobody typed. `faucets_minute` moves it WITHIN the hour
        # only — off the hour boundary by default, because that is when every
        # other sweep on the platform wakes up.
        #
        # Idempotent per member per hour (assets.FaucetPayout carries the unique
        # key), so a double fire is free and a retry pays nobody twice. A missed
        # hour is never backfilled: the task reads the clock rather than taking
        # an argument, which is what stops a worker coming back after a day out
        # and paying twenty-four hours at once.
        schedule["faucet-hourly-payout"] = {
            "task": "toto.assets.tasks.run_faucet_hour",
            "schedule": crontab(minute=str(faucets_minute)),
        }

    if subscriptions:
        from celery.schedules import crontab

        # Daily, for a MONTHLY charge — deliberately. A monthly beat that misses
        # its one firing misses a month; this one catches up the next morning
        # because `materialize` is idempotent by (subscription, period_label)
        # and a day with nothing due creates nothing. After the levy, so an
        # empty wallet is reported by whichever ran first rather than by both.
        schedule["subscriptions-monthly-billing"] = {
            "task": "toto.subscriptions.tasks.run_billing",
            "schedule": crontab(hour=subscriptions_hour, minute=subscriptions_minute),
        }

    if sweep:
        from celery.schedules import crontab

        # The safety net for every run table: closes rows whose worker died
        # without a finally-block. Hourly, off the :00 stampede (monit-prune's
        # :17 reasoning). Idempotent — a closed row never matches again.
        schedule["quota-sweep-stuck-runs"] = {
            "task": "toto.quota.tasks.sweep_stuck_runs",
            "schedule": crontab(minute="41"),
        }

    if anastasia:
        # Expire lapsed reservations, tell the manager which Gears still exist,
        # and fold each mounted Gear's live sample onto its row.
        #
        # Minutes, not hours: this is what makes a stale reading visible. A
        # Gear whose manager has stopped answering must show as DEGRADED while
        # somebody can still act on it, and the staleness threshold is three
        # minutes by default — a schedule slower than that would mean every
        # Gear looked degraded between ticks.
        schedule["anastasia-reconcile"] = {
            "task": "toto.anastasia.tasks.reconcile",
            "schedule": float(anastasia_minutes) * 60.0,
        }

    return schedule
