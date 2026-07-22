from django.urls import path

from .views import (
    WorkspaceCreateView,
    WorkspaceIndexView,
    compile_latex,
    compile_status,
)

app_name = "texlab"

urlpatterns = [
    path("",                             WorkspaceIndexView.as_view(),  name="index"),
    path("new/",                         WorkspaceCreateView.as_view(), name="create"),
    path("file/<int:file_pk>/compile/",  compile_latex,               name="compile_latex"),
    path("compile/<int:run_id>/status/", compile_status,              name="compile_status"),
]
