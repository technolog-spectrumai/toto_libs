from django.urls import path

from . import views

app_name = "claims"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("entitlements/", views.entitlement_list, name="entitlement_list"),
    path("entitlements/<uuid:uuid>/", views.entitlement_detail, name="entitlement_detail"),
    path("obligations/", views.obligation_list, name="obligation_list"),
    path("obligations/<int:pk>/", views.obligation_detail, name="obligation_detail"),
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("schedules/<uuid:uuid>/", views.schedule_detail, name="schedule_detail"),
    path("conditions/", views.condition_list, name="condition_list"),
    path("conditions/<uuid:uuid>/", views.condition_detail, name="condition_detail"),
    path("allocations/", views.allocation_list, name="allocation_list"),
    path("allocations/<uuid:uuid>/", views.allocation_detail, name="allocation_detail"),
    path("events/", views.event_list, name="event_list"),
    path("events/<uuid:uuid>/", views.event_detail, name="event_detail"),
    path("graph/", views.lifecycle_graph_json, name="lifecycle_graph_json"),
    # Transitions
    path("entitlements/<uuid:uuid>/transition/", views.entitlement_transition, name="entitlement_transition"),
    path("schedules/<uuid:uuid>/transition/", views.schedule_transition, name="schedule_transition"),
    path("conditions/<uuid:uuid>/transition/", views.condition_transition, name="condition_transition"),
    path("allocations/<uuid:uuid>/transition/", views.allocation_transition, name="allocation_transition"),
]
