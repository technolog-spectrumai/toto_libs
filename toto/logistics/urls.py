from django.urls import path
from . import views

app_name = "logistics"

urlpatterns = [
    path("logistics/", views.package_list, name="package-list"),
    path("logistics/track/<str:tracking_number>/", views.package_tracking, name="package-tracking"),
    path("logistics/api/export-layers/", views.api_export_layers, name="api-export-layers"),
]
