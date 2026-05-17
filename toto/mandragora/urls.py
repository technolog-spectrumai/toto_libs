from django.urls import path
from .views import (
    NotebookListView, NotebookDetailView,
    run_cell,
    start_kernel, stop_kernel, check_kernel, kernel_dependencies,
    create_cell, delete_cell, promote_cell_to_lambda,
)
from .workflow_views import (
    # API
    workflow_list, workflow_detail, validate_workflow,
    node_create, node_detail,
    edge_create, edge_delete,
    run_list, run_detail,
    human_task_submit,
    # UI
    WorkflowListUIView, WorkflowDetailUIView,
    WorkflowRunDetailUIView,
)

app_name = "mandragora"

urlpatterns = [
    # ----- Notebook -----
    path("", NotebookListView.as_view(), name="notebook_list"),
    path("cells/<int:cell_id>/run/", run_cell, name="run_cell"),
    path("kernel/<int:notebook_id>/start/", start_kernel, name="start_kernel"),
    path("kernel/<int:notebook_id>/stop/", stop_kernel, name="stop_kernel"),
    path("kernel/<int:notebook_id>/status/", check_kernel, name="check_kernel"),
    path("kernel/<int:notebook_id>/dependencies/", kernel_dependencies, name="kernel_dependencies"),
    path("<int:notebook_id>/cells/create/", create_cell, name="create_cell"),
    path("cells/<int:cell_id>/delete/", delete_cell, name="delete_cell"),
    path("cell/<int:cell_id>/promote/", promote_cell_to_lambda, name="promote_cell"),

    # ----- Workflow UI -----
    path("workflows/", WorkflowListUIView.as_view(), name="workflow_list"),
    path("workflows/<int:workflow_id>/", WorkflowDetailUIView.as_view(), name="workflow_detail"),
    path("workflows/runs/<int:run_id>/", WorkflowRunDetailUIView.as_view(), name="workflow_run_detail"),

    # ----- Workflow API -----
    path("api/workflows/", workflow_list, name="api_workflow_list"),
    path("api/workflows/<int:workflow_id>/", workflow_detail, name="api_workflow_detail"),
    path("api/workflows/<int:workflow_id>/validate/", validate_workflow, name="api_workflow_validate"),
    path("api/workflows/<int:workflow_id>/nodes/", node_create, name="api_node_create"),
    path("api/workflows/<int:workflow_id>/nodes/<int:node_id>/", node_detail, name="api_node_detail"),
    path("api/workflows/<int:workflow_id>/edges/", edge_create, name="api_edge_create"),
    path("api/workflows/<int:workflow_id>/edges/<int:edge_id>/", edge_delete, name="api_edge_delete"),
    path("api/workflows/<int:workflow_id>/runs/", run_list, name="api_run_list"),
    path("api/runs/<int:run_id>/", run_detail, name="api_run_detail"),
    path("api/human-tasks/<int:task_id>/submit/", human_task_submit, name="api_human_task_submit"),

    # ----- Notebook detail (slug catch-all — must be last) -----
    path("<slug:slug>/", NotebookDetailView.as_view(), name="notebook_detail"),
]
