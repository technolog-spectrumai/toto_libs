from django.urls import path
from . import views

app_name = "field"

urlpatterns = [
    path("", views.CommandView.as_view(), name="command"),
    path("feed/", views.FeedView.as_view(), name="feed"),
    path("metrics/", views.MetricsView.as_view(), name="metrics"),
    path("api/map/", views.MapDataView.as_view(), name="api_map"),
]
