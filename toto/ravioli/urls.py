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
    graph_export_preview,
    graph_export_apply,
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
    path("queries/<int:query_id>/export-neojson/", export_query_neojson_view, name="export_query_neojson"),
    path("search/", search_view, name="search"),
    path("search/status/<int:run_id>/", search_status_view, name="search_status"),
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
]
