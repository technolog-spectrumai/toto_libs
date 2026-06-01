from django.urls import path

from .views import (
    # API
    report_template_list, report_template_detail, report_list, report_detail,
    workflow_list, workflow_detail, validate_workflow,
    node_create, node_detail,
    edge_create, edge_delete,
    run_list, run_detail, cancel_run,
    # UI
    WorkflowListUIView, WorkflowDetailUIView,
    WorkflowRunDetailUIView,
)

app_name = "workflows"

urlpatterns = [
    # ----- UI -----
    path("", WorkflowListUIView.as_view(), name="workflow_list"),
    path("runs/<int:run_id>/", WorkflowRunDetailUIView.as_view(), name="workflow_run_detail"),
    path("<int:workflow_id>/", WorkflowDetailUIView.as_view(), name="workflow_detail"),

    # ----- API -----
    path("api/report-templates/", report_template_list, name="api_report_template_list"),
    path("api/report-templates/<int:template_id>/", report_template_detail, name="api_report_template_detail"),
    path("api/reports/", report_list, name="api_report_list"),
    path("api/reports/<int:report_id>/", report_detail, name="api_report_detail"),
    path("api/", workflow_list, name="api_workflow_list"),
    path("api/<int:workflow_id>/", workflow_detail, name="api_workflow_detail"),
    path("api/<int:workflow_id>/validate/", validate_workflow, name="api_workflow_validate"),
    path("api/<int:workflow_id>/nodes/", node_create, name="api_node_create"),
    path("api/<int:workflow_id>/nodes/<int:node_id>/", node_detail, name="api_node_detail"),
    path("api/<int:workflow_id>/edges/", edge_create, name="api_edge_create"),
    path("api/<int:workflow_id>/edges/<int:edge_id>/", edge_delete, name="api_edge_delete"),
    path("api/<int:workflow_id>/runs/", run_list, name="api_run_list"),
    path("api/runs/<int:run_id>/", run_detail, name="api_run_detail"),
    path("api/runs/<int:run_id>/cancel/", cancel_run, name="api_cancel_run"),
]
