from django.urls import path
from .views import (
    apply_projection_plan_view,
    clear_db_view,
    create_projection_plan,
    full_sync_view,
    graph_analysis_view,
    graph_analysis_status_view,
    start_graph_analysis_view,
    query_unified_view,
    query_graph_data,
    query_cached_data,
    run_cypher_query_view,
    projection_plan_detail,
    projection_sync_view,
    run_projection_stream,
)

app_name = "ravioli"

urlpatterns = [
    path("graph-analysis/", graph_analysis_view, name="graph_analysis"),
    path("graph-analysis/start/", start_graph_analysis_view, name="start_graph_analysis"),
    path("graph-analysis/status/<int:run_id>/", graph_analysis_status_view, name="graph_analysis_status"),
    path("queries/unified/", query_unified_view, name="query_unified"),
    path("queries/<int:query_id>/data/", query_graph_data, name="query_graph_data"),
    path("queries/<int:query_id>/cached/", query_cached_data, name="query_cached_data"),
    path("queries/<int:query_id>/run/", run_cypher_query_view, name="run_cypher_query"),
    path("projections/", projection_sync_view, name="projection_sync"),
    path("projections/plans/", create_projection_plan, name="create_projection_plan"),
    path(
        "projections/plans/<int:plan_id>/",
        projection_plan_detail,
        name="projection_plan_detail",
    ),
    path(
        "projections/plans/<int:plan_id>/apply/",
        apply_projection_plan_view,
        name="apply_projection_plan",
    ),
    path("projections/run-stream/", run_projection_stream, name="run_projection_stream"),
    path("projections/full-sync/", full_sync_view, name="full_sync"),
    path("projections/clear-db/", clear_db_view, name="clear_db"),
]
