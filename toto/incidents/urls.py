from django.urls import path
from . import views

app_name = "incidents"

urlpatterns = [
    path("", views.IncidentListView.as_view(), name="incident-list"),
    path("new/", views.incident_create, name="incident-create"),
    path("dashboard/", views.IncidentDashboardView.as_view(), name="dashboard"),
    path("<uuid:pk>/", views.IncidentDetailView.as_view(), name="incident-detail"),
    path("<uuid:pk>/status/", views.api_update_status, name="api-update-status"),
    path("api/import/", views.api_import_incidents, name="api-import"),
    path("api/promote/<uuid:detection_pk>/", views.api_promote_detection, name="api-promote"),
]
