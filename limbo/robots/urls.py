from django.urls import path

from . import views

app_name = "robots"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("map/", views.map_view, name="map"),
    path("api/map/", views.map_api, name="map_api"),

    path("robots/", views.robot_list, name="robot_list"),
    path("robots/new/", views.robot_create, name="robot_create"),
    path("robots/<int:pk>/", views.robot_detail, name="robot_detail"),
    path("robots/<int:pk>/edit/", views.robot_update, name="robot_update"),
    path("robots/<int:pk>/commission/", views.robot_commission, name="robot_commission"),
    path("robots/<int:pk>/retire/", views.robot_retire, name="robot_retire"),
    path("robots/<int:pk>/commands/new/", views.robot_command_create, name="robot_command_create"),
    path("robots/<int:pk>/telemetry/new/", views.robot_telemetry_create, name="robot_telemetry_create"),

    path("fleets/", views.fleet_list, name="fleet_list"),
    path("fleets/new/", views.fleet_create, name="fleet_create"),
    path("fleets/<int:pk>/", views.fleet_detail, name="fleet_detail"),
    path("fleets/<int:pk>/edit/", views.fleet_update, name="fleet_update"),

    path("missions/", views.mission_list, name="mission_list"),
    path("missions/new/", views.mission_create, name="mission_create"),
    path("missions/<int:pk>/", views.mission_detail, name="mission_detail"),
    path("missions/<int:pk>/edit/", views.mission_update, name="mission_update"),
    path("missions/<int:pk>/dispatch/", views.mission_dispatch, name="mission_dispatch"),
    path("missions/<int:pk>/cancel/", views.mission_cancel, name="mission_cancel"),
    path("missions/<int:pk>/assignments/new/", views.mission_assignment_create, name="mission_assignment_create"),
    path("missions/<int:pk>/waypoints/new/", views.mission_waypoint_create, name="mission_waypoint_create"),

    path("maintenance/", views.maintenance_list, name="maintenance_list"),
    path("maintenance/new/", views.maintenance_create, name="maintenance_create"),

    path("models/", views.model_list, name="model_list"),
    path("models/new/", views.model_create, name="model_create"),

    path("docks/", views.dock_list, name="dock_list"),
    path("docks/new/", views.dock_create, name="dock_create"),

    path("metrics/", views.metrics_view, name="metrics"),
]
