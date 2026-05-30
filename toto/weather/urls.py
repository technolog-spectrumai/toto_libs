from django.urls import path
from . import views

app_name = "weather"

urlpatterns = [
    path("", views.weather_index, name="index"),
    path("api/refresh/", views.api_refresh_weather, name="api_refresh"),
    path("api/current/", views.api_current_data, name="api_current"),
    path("api/forecast/load/", views.api_load_forecast, name="api_load_forecast"),
    path("api/forecast/", views.api_forecast_data, name="api_forecast"),
    path("api/run/<int:run_id>/status/", views.api_run_status, name="api_run_status"),
    path("api/export/", views.api_export_layers, name="api_export_layers"),
]
