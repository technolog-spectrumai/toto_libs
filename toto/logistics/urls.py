from django.urls import path
from . import views

app_name = "logistics"

urlpatterns = [
    path("logistics/", views.package_list, name="package-list"),
    path("logistics/track/<str:tracking_number>/", views.package_tracking, name="package-tracking"),
    path("logistics/fleet/", views.fleet, name="fleet"),
    path("logistics/fleet/map/", views.fleet_map, name="fleet-map"),
    path("logistics/fleet/geojson/", views.fleet_geojson, name="fleet-geojson"),
]
