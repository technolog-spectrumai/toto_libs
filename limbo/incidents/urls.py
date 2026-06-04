from django.urls import path
from . import views
from .api_views import IncidentsMapApiView, PromoteDetectionApiView, IncidentDetailApiView

app_name = "incidents"

urlpatterns = [
    path("api/enigma/list/", IncidentsMapApiView.as_view(), name="api_enigma_list"),
    path("api/enigma/promote/<uuid:detection_pk>/", PromoteDetectionApiView.as_view(), name="api_enigma_promote"),
    path("api/enigma/<uuid:pk>/", IncidentDetailApiView.as_view(), name="api_enigma_detail"),
    path("", views.IncidentListView.as_view(), name="incident-list"),
    path("new/", views.incident_create, name="incident-create"),
    path("dashboard/", views.IncidentDashboardView.as_view(), name="dashboard"),
    path("<uuid:pk>/", views.IncidentDetailView.as_view(), name="incident-detail"),
    path("<uuid:pk>/status/", views.api_update_status, name="api-update-status"),
    path("api/import/", views.api_import_incidents, name="api-import"),
    path("api/promote/<uuid:detection_pk>/", views.api_promote_detection, name="api-promote"),
]
