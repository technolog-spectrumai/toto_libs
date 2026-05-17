from django.urls import path
from toto.texlab.views import (
    compile_status,
    compile_history_json,
    WorkspaceListView,
    WorkspaceDetailView,
    FileDisplayView,
    save_file,
    compile_latex,
    create_workspace,
    #delete_workspace,
    create_file,
    delete_file,
    delete_workspace
)

app_name = "texlab"


urlpatterns = [
    path("", WorkspaceListView.as_view(), name="workspace_list"),
    path("workspace/<slug:slug>/", WorkspaceDetailView.as_view(), name="workspace_detail"),
    path("workspace/<slug:slug>/file/<int:file_id>/", FileDisplayView.as_view(), name="file_display"),
    path("file/<int:file_id>/save/", save_file, name="save_file"),
    path("file/<int:file_id>/compile/", compile_latex, name="compile_latex"),
    path("compile/<int:run_id>/status/", compile_status, name="compile_status"),
    path("file/<int:file_id>/compile/history/", compile_history_json, name="compile_history"),

    path("api/workspace/create/", create_workspace, name="workspace_create_json"),
    # path("api/workspace/<slug:slug>/delete/", delete_workspace, name="workspace_delete_json"),
    path("api/<slug:workspace_slug>/file/create/", create_file, name="create_file"),
    path("file/<int:file_id>/delete/", delete_file, name="delete_file"),
    path("api/workspace/<slug:slug>/delete/", delete_workspace, name="workspace_delete_json"),
]
