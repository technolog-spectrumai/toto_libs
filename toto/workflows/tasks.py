from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .models import WorkflowRun
from .services.executor import WorkflowExecutor


@shared_task(bind=True)
def start_workflow_run_task(self, run_id):
    run = WorkflowRun.objects.select_related("workflow").get(pk=run_id)
    WorkflowExecutor(async_lambdas=True).start(run)
    run.refresh_from_db()
    return {"run_id": run.id, "status": run.status}


@shared_task(bind=True)
def execute_lambda_node_task(self, node_run_id):
    executor = WorkflowExecutor(async_lambdas=True)
    try:
        output = executor.execute_lambda_node_run(node_run_id)
    except SoftTimeLimitExceeded:
        executor.mark_node_run_failed(node_run_id, "Lambda task timed out.")
        raise
    except Exception as exc:
        executor.mark_node_run_failed(node_run_id, str(exc))
        raise
    return {
        "node_run_id": node_run_id,
        "status": "completed",
        "output": output,
    }
