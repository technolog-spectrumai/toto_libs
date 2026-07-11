"""The dedicated TeX compile workflow.

Compilation runs as a ``toto.workflows`` workflow (slug ``texlab-compile-latex``):
a single ``predefined_task`` node that runs :func:`toto.texlab.predefined_tasks.
texlab_compile_latex` (pdflatex → sibling PDF vault file) on a Celery worker. This
helper get-or-creates that workflow so the compile path works without a separate
ingress step; ``ingress_texlab`` also calls it to pre-seed.
"""
from __future__ import annotations

COMPILE_WORKFLOW_SLUG = "texlab-compile-latex"
COMPILE_TASK_NAME = "texlab_compile_latex"


def ensure_compile_workflow():
    """Get-or-create the dedicated TeX compile workflow (idempotent)."""
    from toto.workflows.models import Workflow, WorkflowNode

    wf, _ = Workflow.objects.get_or_create(
        slug=COMPILE_WORKFLOW_SLUG,
        defaults={
            "name": "TeX Compiler",
            "description": "Compile a .tex vault file to PDF via pdflatex (Celery).",
        },
    )
    if not wf.nodes.filter(
        node_type=WorkflowNode.PREDEFINED_TASK, task_name=COMPILE_TASK_NAME
    ).exists():
        WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=COMPILE_TASK_NAME,
            label="compile latex",
        )
    return wf
