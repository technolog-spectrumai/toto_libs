from django.urls import path
from . import views

app_name = "mobilization"

urlpatterns = [
    path("", views.overview, name="overview"),

    # Responders
    path("responders/", views.responder_list, name="responder_list"),
    path("responders/<int:pk>/", views.responder_detail, name="responder_detail"),

    # Reports
    path("reports/", views.report_list, name="report_list"),
    path("reports/new/", views.report_create, name="report_create"),
    path("reports/<int:pk>/", views.report_detail, name="report_detail"),
    path("reports/<int:pk>/submit/", views.report_submit, name="report_submit"),
    path("reports/<int:pk>/review/", views.report_review, name="report_review"),
    path("reports/<int:pk>/enact/", views.report_enact, name="report_enact"),
    path("reports/<int:pk>/reject/", views.report_reject, name="report_reject"),

    # Events
    path("events/", views.event_list, name="event_list"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/deployment/new/", views.deployment_create, name="deployment_create"),
    path("events/<int:pk>/map-data/", views.event_map_data, name="event_map_data"),

    # Deployments
    path("deployments/", views.deployment_list, name="deployment_list"),
    path("deployments/<int:pk>/", views.deployment_detail, name="deployment_detail"),
    path("deployments/<int:pk>/assign/", views.assignment_create, name="assignment_create"),
    path("deployments/<int:pk>/activate/<int:assignment_pk>/", views.assignment_activate, name="assignment_activate"),
    path("deployments/<int:pk>/release/<int:assignment_pk>/", views.assignment_release, name="assignment_release"),
    path("deployments/<int:pk>/complete/", views.deployment_complete, name="deployment_complete"),
    path("deployments/<int:pk>/intervention/new/", views.intervention_create, name="intervention_create"),
    path("interventions/<int:pk>/done/", views.intervention_complete, name="intervention_complete"),
    path("interventions/<int:pk>/review/", views.intervention_review, name="intervention_review"),

    # Evacuation routes
    path("events/<int:pk>/evac-routes/add/", views.evac_route_add, name="evac_route_add"),
    path("events/<int:pk>/evac-routes/<int:route_pk>/status/", views.evac_route_status, name="evac_route_status"),

    # Deployment routes
    path("deployments/<int:pk>/routes/add/", views.deployment_route_add, name="deployment_route_add"),

    # Deployment equipment
    path("deployments/<int:pk>/equipment/add/", views.deployment_equipment_add, name="deployment_equipment_add"),
]
