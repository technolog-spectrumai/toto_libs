import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from toto.workflows.models import WorkflowNodeRun
from toto.workflows.services.executor import WorkflowExecutor

from .models import MediaJob
from .runner import MediaJobRunner
from .workflow import build_job_from_node_run, make_output_envelope

log = logging.getLogger(__name__)


def _run_task(self, node_run_id: int, task_name: str) -> dict:
    node_run = WorkflowNodeRun.objects.select_related(
        "node", "workflow_run"
    ).get(pk=node_run_id)

    job = build_job_from_node_run(node_run)

    try:
        runner = MediaJobRunner()
        runner.run(job)
    except SoftTimeLimitExceeded:
        job.refresh_from_db()
        job.status = MediaJob.Status.FAILED
        job.error_message = "Task timed out (soft limit)"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        executor = WorkflowExecutor(async_lambdas=True)
        executor.mark_node_run_failed(node_run_id, "videomant task timed out")
        raise
    except Exception as exc:
        log.exception("videomant task %s failed for node_run %s: %s", task_name, node_run_id, exc)
        executor = WorkflowExecutor(async_lambdas=True)
        executor.mark_node_run_failed(node_run_id, str(exc))
        raise

    job.refresh_from_db()
    output = make_output_envelope(job)
    job.output_metadata = output.get("data", {})
    job.save(update_fields=["output_metadata"])

    executor = WorkflowExecutor(async_lambdas=True)
    executor.complete_predefined_node_run(node_run_id, output)
    return output


@shared_task(bind=True, name="toto.videomant.tasks.probe", time_limit=120, soft_time_limit=100)
def probe(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.probe")


@shared_task(bind=True, name="toto.videomant.tasks.compress", time_limit=7300, soft_time_limit=7200)
def compress(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.compress")


@shared_task(bind=True, name="toto.videomant.tasks.resize", time_limit=7300, soft_time_limit=7200)
def resize(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.resize")


@shared_task(bind=True, name="toto.videomant.tasks.cut", time_limit=7300, soft_time_limit=7200)
def cut(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.cut")


@shared_task(bind=True, name="toto.videomant.tasks.extract_mp3", time_limit=3700, soft_time_limit=3600)
def extract_mp3(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.extract_mp3")


@shared_task(bind=True, name="toto.videomant.tasks.thumbnail", time_limit=120, soft_time_limit=100)
def thumbnail(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.thumbnail")


@shared_task(bind=True, name="toto.videomant.tasks.gif", time_limit=1200, soft_time_limit=1100)
def gif(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.gif")


@shared_task(bind=True, name="toto.videomant.tasks.concat", time_limit=7300, soft_time_limit=7200)
def concat(self, node_run_id: int) -> dict:
    return _run_task(self, node_run_id, "videomant.concat")
