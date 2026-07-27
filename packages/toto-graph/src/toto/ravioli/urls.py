from django.urls import path
from .views import (
    graph_analysis_view,
    graph_analysis_status_view,
    start_graph_analysis_view,
    query_unified_view,
    query_graph_data,
    query_cached_data,
    run_cypher_query_view,
    export_query_neojson_view,
    search_view,
    search_status_view,
    graphrag_describe_view,
    start_graphrag_describe_view,
    graphrag_describe_status_view,
    graph_export_preview,
    graph_export_apply,
    graph_sync_plan,
    graph_prune_plan,
    graph_plan_apply,
    graph_health_view,
    history_view,
    history_data,
)
from .api_views import KgQueryListApiView, KgQueryRunApiView

app_name = "ravioli"

urlpatterns = [
    # CORS + data_mesh-gated JSON API for the Aurora knowledge-graph view.
    path("api/queries/", KgQueryListApiView.as_view(), name="api_kg_query_list"),
    path("api/queries/<int:query_id>/run/", KgQueryRunApiView.as_view(), name="api_kg_query_run"),
    path("graph-analysis/", graph_analysis_view, name="graph_analysis"),
    path("graph-analysis/start/", start_graph_analysis_view, name="start_graph_analysis"),
    path("graph-analysis/status/<int:run_id>/", graph_analysis_status_view, name="graph_analysis_status"),
    path("queries/unified/", query_unified_view, name="query_unified"),
    path("queries/<int:query_id>/data/", query_graph_data, name="query_graph_data"),
    path("queries/<int:query_id>/cached/", query_cached_data, name="query_cached_data"),
    path("queries/<int:query_id>/run/", run_cypher_query_view, name="run_cypher_query"),
    path("queries/<int:query_id>/export-neojson/", export_query_neojson_view, name="export_query_neojson"),
    path("search/", search_view, name="search"),
    path("search/status/<int:run_id>/", search_status_view, name="search_status"),
    path("describe/", graphrag_describe_view, name="graphrag_describe"),
    path("describe/start/", start_graphrag_describe_view, name="graphrag_describe_start"),
    path("describe/status/<int:run_id>/", graphrag_describe_status_view, name="graphrag_describe_status"),
    path(
        "export/<str:app_label>/<str:model_name>/<str:object_uuid>/",
        graph_export_preview,
        name="graph_export_preview",
    ),
    path(
        "export/<str:app_label>/<str:model_name>/<str:object_uuid>/apply/",
        graph_export_apply,
        name="graph_export_apply",
    ),
    path("graph-sync/plan/", graph_sync_plan, name="graph_sync_plan"),
    path("graph-prune/plan/", graph_prune_plan, name="graph_prune_plan"),
    path("graph/plan/<int:plan_id>/apply/", graph_plan_apply, name="graph_plan_apply"),
    path("graph-health/", graph_health_view, name="graph_health"),
    path("history/", history_view, name="history"),
    path("history/data/", history_data, name="history_data"),
]
