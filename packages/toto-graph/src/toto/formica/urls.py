from django.urls import path

from . import views

app_name = "formica"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("params/", views.params_view, name="params"),
    path("cycles/", views.cycles, name="cycles"),
    path("map/", views.pheromone_map, name="map"),
    path("proposals/", views.proposals, name="proposals"),
    path("proposals/<int:pk>/", views.proposal_detail, name="proposal_detail"),
    # JSON endpoints
    path("api/run-now/", views.api_run_now, name="api_run_now"),
    path("api/pause/", views.api_pause, name="api_pause"),
    path("api/trusted/", views.api_trusted, name="api_trusted"),
    path("api/cycle-chart/", views.api_cycle_chart, name="api_cycle_chart"),
    path("api/pheromone-map/", views.api_pheromone_map, name="api_pheromone_map"),
    path(
        "api/proposals/<int:pk>/ops/<str:op_id>/",
        views.api_proposal_op,
        name="api_proposal_op",
    ),
    path(
        "api/proposals/<int:pk>/apply/",
        views.api_proposal_apply,
        name="api_proposal_apply",
    ),
]
