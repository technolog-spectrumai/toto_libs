from django.urls import path
from . import views
from .api_views import MobilizationMapApiView

app_name = "mobilization"

urlpatterns = [
    path("api/enigma/map/", MobilizationMapApiView.as_view(), name="api_enigma_map"),
    path("", views.overview, name="overview"),

    # Responders
    path("responders/", views.responder_list, name="responder_list"),
    path("responders/recruit/", views.responder_recruit, name="responder_recruit"),
    path("responders/call-in/", views.responder_callin, name="responder_callin"),
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

    # Evacuation routes
    path("events/<int:pk>/evac-routes/add/", views.evac_route_add, name="evac_route_add"),
    path("events/<int:pk>/evac-routes/<int:route_pk>/status/", views.evac_route_status, name="evac_route_status"),

    # Emergency status — declaration must pass through assembly
    path("events/<int:pk>/emergency/propose/", views.emergency_status_propose, name="emergency_status_propose"),
    path("events/<int:pk>/emergency/proposal/<int:proposal_pk>/activate/", views.emergency_proposal_activate, name="emergency_proposal_activate"),
    path("events/<int:pk>/emergency/<int:es_pk>/lift/", views.emergency_status_lift, name="emergency_status_lift"),
    path("events/<int:pk>/emergency/<int:es_pk>/equipment/", views.emergency_equipment_authorize, name="emergency_equipment_authorize"),
]
