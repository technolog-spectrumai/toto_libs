from django.urls import path
from .views import (
    query_unified_view,
    query_graph_data,
    projection_sync_view,
    run_projection_stream,
)

app_name = "ravioli"

urlpatterns = [
    path("queries/unified/", query_unified_view, name="query_unified"),
    path("queries/<int:query_id>/data/", query_graph_data, name="query_graph_data"),
    path("projections/", projection_sync_view, name="projection_sync"),
    path("projections/run-stream/", run_projection_stream, name="run_projection_stream"),
]
