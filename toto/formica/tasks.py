"""Celery topology: beat scan → one cycle task per due colony → proposal apply.

Exactly one live cycle per colony: ``run_colony_cycle`` claims the colony row
with ``select_for_update`` and no-ops when a pending/running cycle exists, so
overlapping beat ticks and manual triggers are safe.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="toto.formica.tasks.formica_beat_scan")
def formica_beat_scan(self):
    """Dispatch a cycle for every enabled, unpaused, due colony."""
    from .models import Colony, ColonyCycle

    now = timezone.now()
    dispatched, skipped = 0, 0
    for colony_id in Colony.objects.filter(enabled=True, paused=False).values_list(
        "pk", flat=True
    ):
        with transaction.atomic():
            colony = (
                Colony.objects.select_for_update(skip_locked=True)
                .filter(pk=colony_id)
                .first()
            )
            if colony is None or not colony.is_due(now):
                continue
            if colony.has_cycle_in_flight():
                skipped += 1
                continue
            cycle = _create_cycle(colony, triggered_by=ColonyCycle.TRIGGERED_BEAT)
            # Claim the tick before dispatch so a slow cycle can't double-fire.
            colony.last_run_at = now
            colony.save(update_fields=["last_run_at"])
        run_colony_cycle.delay(cycle.pk)
        dispatched += 1
    return {"dispatched": dispatched, "skipped": skipped}


def _create_cycle(colony, *, triggered_by):
    """Create the next sequential cycle row (caller holds the colony lock)."""
    import secrets

    from .models import ColonyCycle

    last = colony.cycles.order_by("-number").first()
    return ColonyCycle.objects.create(
        colony=colony,
        number=(last.number + 1) if last else 1,
        triggered_by=triggered_by,
        rng_seed=secrets.randbits(48),
    )


def trigger_cycle(colony, *, triggered_by="manual"):
    """Manual 'Run cycle now': create + dispatch (or run inline without celery).

    Returns ``(cycle, queued)``; raises ``CycleInProgress`` when one is live.
    """
    from toto.celery_utils import celery_available

    with transaction.atomic():
        locked = (
            type(colony).objects.select_for_update().filter(pk=colony.pk).first()
        )
        if locked.has_cycle_in_flight():
            raise CycleInProgress(
                f"Colony '{colony.slug}' already has a cycle pending or running."
            )
        cycle = _create_cycle(locked, triggered_by=triggered_by)
        locked.last_run_at = timezone.now()
        locked.save(update_fields=["last_run_at"])
    if celery_available():
        run_colony_cycle.delay(cycle.pk)
        return cycle, True
    run_colony_cycle_inline(cycle.pk)
    return cycle, False


class CycleInProgress(RuntimeError):
    pass


@shared_task(bind=True, name="toto.formica.tasks.run_colony_cycle", max_retries=0)
def run_colony_cycle(self, cycle_id):
    run_colony_cycle_inline(cycle_id)


def run_colony_cycle_inline(cycle_id):
    from .colony.cycle import execute_cycle

    execute_cycle(cycle_id)


@shared_task(bind=True, name="toto.formica.tasks.apply_formica_proposal")
def apply_formica_proposal(self, proposal_id, user_id=None):
    from .services.apply import run as apply_run

    apply_run(proposal_id, user_id=user_id)
