from django.urls import path

from . import views

app_name = "nomad"

urlpatterns = [
    path("migrate", views.MigrateOnionView.as_view(), name="migrate"),
    path("reachability", views.SetReachabilityView.as_view(), name="set_reachability"),
    path("connect_qr.png", views.ConnectQrView.as_view(), name="connect_qr"),
]
