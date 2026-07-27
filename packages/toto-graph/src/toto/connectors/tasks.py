"""Celery tasks: per-run execution + the global schedule scan (beat).

Scheduling is a single beat task scanning ``DataConnector`` rows (the weather
pattern) — no per-object beat entries, no django-celery-beat. ``next_run_at``
is advanced *before* dispatch inside a row lock, so a slow run can never make
the same tick fire twice.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="toto.connectors.tasks.run_connector_task")
def run_connector_task(self, run_id):
    """Execute one ConnectorRun. Never retried — a retry would double-fetch."""
    from .models import ConnectorRun
    from .services.runner import execute_run

    ConnectorRun.objects.filter(pk=run_id).update(task_id=self.request.id or "")
    execute_run(run_id)


@shared_task(bind=True, name="toto.connectors.tasks.connectors_scan_schedules")
def connectors_scan_schedules(self):
    """Dispatch a run for every due, schedule-enabled connector."""
    from .models import ConnectorRun, DataConnector
    from .services.runner import RunInProgress, create_run

    now = timezone.now()
    dispatched, skipped = 0, 0

    due_ids = list(
        DataConnector.objects.filter(
            is_active=True,
            schedule_enabled=True,
            next_run_at__lte=now,
        ).values_list("pk", flat=True)
    )
    for connector_id in due_ids:
        with transaction.atomic():
            connector = (
                DataConnector.objects.select_for_update(skip_locked=True)
                .filter(pk=connector_id)
                .first()
            )
            if (
                connector is None
                or not connector.is_active
                or not connector.schedule_enabled
                or connector.next_run_at is None
                or connector.next_run_at > now
            ):
                continue  # claimed by a concurrent scan or edited meanwhile
            # Claim before dispatch: the tick is consumed even if the run fails.
            connector.next_run_at = connector.schedule_next(now)
            connector.save(update_fields=["next_run_at"])

        if not connector.trusted and connector.has_unreviewed_run():
            # An untrusted connector's output waits for a human; re-fetching
            # before that review lands would pile up near-identical proposals
            # whose 'new' nodes duplicate each other if several get applied.
            skipped += 1
            logger.info(
                "connectors: '%s' has a proposal awaiting review — skipping this tick",
                connector.slug,
            )
            continue
        try:
            run = create_run(
                connector,
                triggered=ConnectorRun.TRIGGERED_SCHEDULE,
                user=connector.owner,
            )
        except RunInProgress:
            skipped += 1
            logger.info(
                "connectors: '%s' still has a run in flight — skipping this tick",
                connector.slug,
            )
            continue
        run_connector_task.delay(run.pk)
        dispatched += 1

    return {"dispatched": dispatched, "skipped": skipped}
