from django.urls import path

from .views import (
    apply_projection_plan_view,
    clear_db_view,
    create_projection_plan,
    full_sync_view,
    projection_plan_detail,
    projection_sync_view,
    run_projection_stream,
)

app_name = "sql_neo4j_sync"

urlpatterns = [
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
