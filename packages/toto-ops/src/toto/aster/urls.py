from django.urls import path

from . import api_views

app_name = "aster"

urlpatterns = [
    path("device", api_views.DeviceView.as_view(), name="device"),
    path("addr", api_views.AddrView.as_view(), name="addr_publish"),
    path("addr/<str:node_id>", api_views.AddrView.as_view(), name="addr_resolve"),
    path("resolve", api_views.ResolveView.as_view(), name="resolve"),
]
