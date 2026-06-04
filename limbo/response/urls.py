from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "response"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="response:deployment_list", permanent=False)),
    # Deployments
    path("deployments/", views.deployment_list, name="deployment_list"),
    path("deployments/<int:pk>/", views.deployment_detail, name="deployment_detail"),
    path("deployments/<int:pk>/map-data/", views.deployment_map_data, name="deployment_map_data"),
    path("deployments/<int:pk>/assign/", views.assignment_create, name="assignment_create"),
    path("deployments/<int:pk>/activate/<int:assignment_pk>/", views.assignment_activate, name="assignment_activate"),
    path("deployments/<int:pk>/release/<int:assignment_pk>/", views.assignment_release, name="assignment_release"),
    path("deployments/<int:pk>/complete/", views.deployment_complete, name="deployment_complete"),
    path("deployments/<int:pk>/intervention/new/", views.intervention_create, name="intervention_create"),
    path("deployments/<int:pk>/routes/add/", views.deployment_route_add, name="deployment_route_add"),
    path("deployments/<int:pk>/equipment/add/", views.deployment_equipment_add, name="deployment_equipment_add"),
    path("deployments/<int:pk>/metrics/", views.deployment_metrics, name="deployment_metrics"),
    path("deployments/<int:pk>/budget/", views.deployment_budget_link, name="deployment_budget_link"),

    # Interventions
    path("interventions/<int:pk>/done/", views.intervention_complete, name="intervention_complete"),
    path("interventions/<int:pk>/review/", views.intervention_review, name="intervention_review"),
]
