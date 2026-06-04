from django.urls import path
from . import views
from .api_views import FleetApiView, PackagesApiView

app_name = "logistics"

urlpatterns = [
    path("api/enigma/fleet/", FleetApiView.as_view(), name="api_fleet"),
    path("api/enigma/packages/", PackagesApiView.as_view(), name="api_packages"),
    path("logistics/", views.package_list, name="package-list"),
    path("logistics/track/<str:tracking_number>/", views.package_tracking, name="package-tracking"),
    path("logistics/fleet/", views.fleet, name="fleet"),
    path("logistics/fleet/map/", views.fleet_map, name="fleet-map"),
    path("logistics/fleet/geojson/", views.fleet_geojson, name="fleet-geojson"),
    path("logistics/fleet/export/", views.api_export_fleet, name="fleet-export"),
    path("logistics/fleet/run/<int:run_id>/status/", views.api_fleet_run_status, name="fleet-run-status"),
]
