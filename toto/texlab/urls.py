from django.urls import path

from .views import (
    FileDisplayView,
    bucket_images_json,
    compile_history_json,
    compile_latex,
    compile_status,
    save_file,
)

app_name = "texlab"

urlpatterns = [
    path("file/<int:file_pk>/",          FileDisplayView.as_view(),   name="file_display"),
    path("file/<int:file_pk>/save/",     save_file,                   name="save_file"),
    path("file/<int:file_pk>/compile/",  compile_latex,               name="compile_latex"),
    path("file/<int:file_pk>/history/",  compile_history_json,        name="compile_history"),
    path("file/<int:file_pk>/images/",   bucket_images_json,          name="bucket_images"),
    path("compile/<int:run_id>/status/", compile_status,              name="compile_status"),
]
