from django.urls import path
from . import views

app_name = "videomant"

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("browse/", views.bucket_list, name="bucket_list"),
    path("browse/<int:bucket_pk>/", views.bucket_file_list, name="bucket_file_list"),
    path("workspaces/", views.workspace_list, name="workspace_list"),
    path("workspaces/create/", views.workspace_create, name="workspace_create"),
    path("workspaces/<slug:slug>/", views.workspace_detail, name="workspace_detail"),
    path("workspaces/<slug:slug>/delete/", views.workspace_delete, name="workspace_delete"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("jobs/<int:pk>/status.json", views.job_status_json, name="job_status_json"),
    path("vault/<int:file_id>/actions/", views.vault_file_actions, name="vault_file_actions"),
    path("vault/<int:file_id>/builder/", views.command_builder, name="command_builder"),
    path("vault/<int:file_id>/compress/", views.enqueue_compress, name="enqueue_compress"),
    path("vault/<int:file_id>/resize/", views.enqueue_resize, name="enqueue_resize"),
    path("vault/<int:file_id>/cut/", views.enqueue_cut, name="enqueue_cut"),
    path("vault/<int:file_id>/extract-mp3/", views.enqueue_extract_mp3, name="enqueue_extract_mp3"),
    path("vault/<int:file_id>/thumbnail/", views.enqueue_thumbnail, name="enqueue_thumbnail"),
    path("vault/<int:file_id>/gif/", views.enqueue_gif, name="enqueue_gif"),
    path("vault/<int:file_id>/concat/", views.enqueue_concat, name="enqueue_concat"),
]
