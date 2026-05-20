from django.urls import path
from .views import (
    apply_projection_plan_view,
    create_projection_plan,
    query_unified_view,
    query_graph_data,
    projection_plan_detail,
    projection_sync_view,
    run_projection_stream,
)

app_name = "ravioli"

urlpatterns = [
    path("queries/unified/", query_unified_view, name="query_unified"),
    path("queries/<int:query_id>/data/", query_graph_data, name="query_graph_data"),
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
]
