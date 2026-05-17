from django.urls import path

from .views import (
    # API
    workflow_list, workflow_detail, validate_workflow,
    node_create, node_detail,
    edge_create, edge_delete,
    run_list, run_detail,
    human_task_submit,
    # UI
    WorkflowListUIView, WorkflowDetailUIView, WorkflowRunDetailUIView,
)

app_name = "workflows"

urlpatterns = [
    # ----- UI -----
    path("", WorkflowListUIView.as_view(), name="workflow_list"),
    path("<int:workflow_id>/", WorkflowDetailUIView.as_view(), name="workflow_detail"),
    path("runs/<int:run_id>/", WorkflowRunDetailUIView.as_view(), name="workflow_run_detail"),

    # ----- API -----
    path("api/", workflow_list, name="api_workflow_list"),
    path("api/<int:workflow_id>/", workflow_detail, name="api_workflow_detail"),
    path("api/<int:workflow_id>/validate/", validate_workflow, name="api_workflow_validate"),
    path("api/<int:workflow_id>/nodes/", node_create, name="api_node_create"),
    path("api/<int:workflow_id>/nodes/<int:node_id>/", node_detail, name="api_node_detail"),
    path("api/<int:workflow_id>/edges/", edge_create, name="api_edge_create"),
    path("api/<int:workflow_id>/edges/<int:edge_id>/", edge_delete, name="api_edge_delete"),
    path("api/<int:workflow_id>/runs/", run_list, name="api_run_list"),
    path("api/runs/<int:run_id>/", run_detail, name="api_run_detail"),
    path("api/human-tasks/<int:task_id>/submit/", human_task_submit, name="api_human_task_submit"),
]
