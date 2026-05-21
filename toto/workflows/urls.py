from django.urls import path

from .views import (
    # API
    connector_list, connector_detail,
    report_template_list, report_template_detail, report_list, report_detail,
    workflow_list, workflow_detail, validate_workflow,
    node_create, node_detail,
    edge_create, edge_delete,
    run_list, run_detail,
    human_task_submit,
    # UI
    ReportListUIView, ReportDetailUIView,
    WorkflowListUIView, WorkflowDetailUIView, WorkflowGateUIView,
    WorkflowRunDetailUIView,
    workflow_run_legacy_redirect, workflow_run_start_ui, workflow_run_restart_ui,
)

app_name = "workflows"

urlpatterns = [
    # ----- UI -----
    path("", WorkflowListUIView.as_view(), name="workflow_list"),
    path("reports/", ReportListUIView.as_view(), name="report_list"),
    path("reports/<int:report_id>/", ReportDetailUIView.as_view(), name="report_detail"),
    path("<int:workflow_id>/gate/", WorkflowGateUIView.as_view(), name="workflow_gate"),
    path("<int:workflow_id>/run/", workflow_run_start_ui, name="workflow_run_start"),
    path("runs/<int:run_id>/restart/", workflow_run_restart_ui, name="workflow_run_restart"),
    path("runs/<int:run_id>/status/", workflow_run_legacy_redirect, name="workflow_run_status"),
    path("runs/<int:run_id>/console/", workflow_run_legacy_redirect, name="workflow_run_console"),
    path("runs/<int:run_id>/", WorkflowRunDetailUIView.as_view(), name="workflow_run_detail"),
    path("<int:workflow_id>/", WorkflowDetailUIView.as_view(), name="workflow_detail"),

    # ----- API -----
    path("api/connectors/", connector_list, name="api_connector_list"),
    path("api/connectors/<int:connector_id>/", connector_detail, name="api_connector_detail"),
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
    path("api/human-tasks/<int:task_id>/submit/", human_task_submit, name="api_human_task_submit"),
]
