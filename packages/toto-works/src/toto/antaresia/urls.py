from django.urls import path

from .views import (
    FileDisplayView,
    delete_file,
    run_history_json,
    run_python,
    run_status,
    save_file,
)

app_name = "antaresia"

urlpatterns = [
    path("file/<int:file_pk>/",         FileDisplayView.as_view(), name="file_display"),
    path("file/<int:file_pk>/save/",    save_file,                 name="save_file"),
    path("file/<int:file_pk>/delete/",  delete_file,               name="delete_file"),
    path("file/<int:file_pk>/run/",     run_python,                name="run_python"),
    path("file/<int:file_pk>/history/", run_history_json,          name="run_history"),
    path("run/<int:run_id>/status/",    run_status,                name="run_status"),
]
