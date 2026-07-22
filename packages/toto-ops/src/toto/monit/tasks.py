import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="toto.monit.tasks.monit_sample", ignore_result=True,
             soft_time_limit=55, time_limit=90)
def monit_sample():
    """Record one Snapshot of system/service/web state (runs in the worker)."""
    from .collectors import collect_all_for_snapshot
    from .models import Snapshot

    snapshot = Snapshot.objects.create(**collect_all_for_snapshot())
    logger.info("monit: snapshot %s recorded", snapshot.pk)


@shared_task(name="toto.monit.tasks.monit_prune", ignore_result=True)
def monit_prune():
    """Drop snapshots older than MONIT_RETENTION_HOURS (default 48)."""
    from .models import Snapshot

    hours = getattr(settings, "MONIT_RETENTION_HOURS", 48)
    cutoff = timezone.now() - timedelta(hours=hours)
    deleted, _ = Snapshot.objects.filter(created__lt=cutoff).delete()
    if deleted:
        logger.info("monit: pruned %d snapshots older than %dh", deleted, hours)
