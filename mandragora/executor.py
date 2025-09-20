from django.utils import timezone
from .models import Workflow, WorkflowRun,NodeRun
from celery import current_app


class WorkflowExecutor:

    @staticmethod
    def start_workflow(workflow_id):
        workflow = Workflow.objects.get(id=workflow_id)
        run = WorkflowRun.objects.create(workflow=workflow, status="running")

        root_nodes = workflow.nodes.exclude(incoming__isnull=False).distinct()

        for node in root_nodes:
            WorkflowExecutor.dispatch_node(run, node, input_data={})

        return run

    @staticmethod
    def dispatch_node(workflow_run, node, input_data):
        node_run = NodeRun.objects.create(
            workflow_run=workflow_run,
            node=node,
            input_data=input_data,
            status="pending"
        )

        current_app.send_task(
            "mandragora.tasks.execute_node_task",
            args=[node_run.id],
            countdown=node.countdown,
            retry=True,
            retry_policy={
                'max_retries': node.max_retries,
                'interval_start': node.countdown,
                'interval_step': node.countdown,
                'interval_max': node.countdown * 3,
            }
        )

    @staticmethod
    def execute_node(node_run_id):
        node_run = NodeRun.objects.select_related("node", "workflow_run").get(id=node_run_id)
        node = node_run.node.get_subclass()
        node_run.status = "running"
        node_run.started_at = timezone.now()
        node_run.save()

        try:
            output = node.execute(node_run.input_data)
            WorkflowExecutor.complete_node_run(node_run, output)
        except Exception as e:
            WorkflowExecutor.fail_node_run(node_run, str(e))

    @staticmethod
    def complete_node_run(node_run, output_data):
        node_run.output_data = output_data
        node_run.status = "success"
        node_run.finished_at = timezone.now()
        node_run.save()

        WorkflowExecutor.trigger_downstream(node_run)
        WorkflowExecutor.check_workflow_completion(node_run.workflow_run)

    @staticmethod
    def fail_node_run(node_run, error_message):
        node_run.status = "failed"
        node_run.error = error_message
        node_run.finished_at = timezone.now()
        node_run.save()

        workflow_run = node_run.workflow_run
        workflow_run.status = "failed"
        workflow_run.error = error_message
        workflow_run.finished_at = timezone.now()
        workflow_run.save()

    @staticmethod
    def trigger_downstream(node_run):
        node = node_run.node
        workflow_run = node_run.workflow_run

        for edge in node.outgoing.select_related("target").all():
            target = edge.target
            incoming_edges = target.incoming.select_related("source").all()

            ready = all(
                NodeRun.objects.filter(
                    workflow_run=workflow_run,
                    node=e.source,
                    status="success"
                ).exists()
                for e in incoming_edges
            )

            if ready:
                input_data = {
                    e.source.id: NodeRun.objects.get(
                        workflow_run=workflow_run,
                        node=e.source
                    ).output_data
                    for e in incoming_edges
                }

                WorkflowExecutor.dispatch_node(workflow_run, target, input_data)

    @staticmethod
    def check_workflow_completion(workflow_run):
        total_nodes = workflow_run.workflow.nodes.count()
        completed_nodes = workflow_run.node_runs.filter(status="success").count()

        if total_nodes == completed_nodes:
            workflow_run.status = "success"
            workflow_run.finished_at = timezone.now()
            workflow_run.save()
