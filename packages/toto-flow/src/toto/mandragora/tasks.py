from celery import shared_task
from django.conf import settings

from .models import Cell
from .kernel import KernelClient

client = KernelClient(addr=getattr(settings, "KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555"))


@shared_task
def execute_cell_task(cell_id):
    cell = Cell.objects.get(id=cell_id)
    notebook = cell.notebook

    response = client.execute(notebook.id, cell.content)

    if "error" in response:
        cell.stderr = response["error"]
        cell.stdout = ""
        cell.rich_output = None
        cell.save(update_fields=["stdout", "stderr", "rich_output"])
        return False

    cell.stdout = response.get("stdout", "")
    cell.stderr = response.get("stderr", "")
    cell.rich_output = response.get("rich_output") or None
    cell.execution_count = response.get("execution_count") or 0
    cell.save(update_fields=["stdout", "stderr", "rich_output", "execution_count"])
    return True
