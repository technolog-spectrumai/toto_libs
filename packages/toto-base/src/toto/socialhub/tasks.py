"""socialhub's one task: building a *Download my data* export (2026-10-01).

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and "toto.socialhub" must stay in
``toto.registry.TASK_MODULES``. The work is ``data_export.build``; this is
only its door onto a worker. ``acks_late=False``: a killed export is closed
as failed by ``data_export.close_stale``, never redelivered to run twice.
"""

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

TASK_NAME = "toto.socialhub.tasks.build_data_export"


@shared_task(name=TASK_NAME, acks_late=False, ignore_result=True,
             soft_time_limit=1800, time_limit=1860)
def build_data_export(export_id):
    from django.utils.translation import gettext as _

    from .data_export import build, fail
    from .models import DataExport

    try:
        build(export_id)
    except SoftTimeLimitExceeded:
        export = DataExport.objects.filter(pk=export_id).first()
        if export is not None:
            fail(export, _("The export ran out of time. Ask an administrator for a copy."))
