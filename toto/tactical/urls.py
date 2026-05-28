from django.urls import path
from . import views

app_name = "tactical"

urlpatterns = [
    path("", views.CommandView.as_view(), name="command"),
    path("metrics/", views.MetricsView.as_view(), name="metrics"),
    path("api/map/", views.MapDataView.as_view(), name="api_map"),
    path("api/metrics/", views.MetricsDataView.as_view(), name="api_metrics"),
]
