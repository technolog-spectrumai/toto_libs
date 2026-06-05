from django.urls import path

from .views import (
    RunDetailView, open_primary_service, run_service, run_status, services_for_file,
)

app_name = "fileservices"

urlpatterns = [
    path("file/<int:file_pk>/services/", services_for_file, name="services_for_file"),
    path("file/<int:file_pk>/open/",     open_primary_service, name="open_primary"),
    path("file/<int:file_pk>/run/",      run_service,       name="run_service"),
    path("runs/<int:run_id>/",           RunDetailView.as_view(), name="run_detail"),
    path("runs/<int:run_id>/status/",    run_status,        name="run_status"),
]
