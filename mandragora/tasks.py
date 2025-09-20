from celery import shared_task
from django.db import transaction
from .executor import WorkflowExecutor


@shared_task(bind=True, max_retries=None)
def execute_node_task(self, node_run_id):
    try:
        with transaction.atomic():
            WorkflowExecutor.execute_node(node_run_id)
    except Exception as exc:
        raise self.retry(exc=exc)
