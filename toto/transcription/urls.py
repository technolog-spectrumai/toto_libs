from django.urls import path

from . import views

app_name = "transcription"

urlpatterns = [
    path("", views.home, name="home"),
    path("collections/", views.collection_list, name="collection_list"),
    path("collections/new/", views.collection_create, name="collection_create"),
    path("upload/", views.upload, name="upload"),
    path("<slug:slug>/", views.collection_detail, name="collection_detail"),
    path("<slug:collection_slug>/<slug:source_slug>/", views.source_detail, name="source_detail"),
    path("<slug:collection_slug>/<slug:source_slug>/manage/", views.source_manage, name="source_manage"),
    path("<slug:collection_slug>/<slug:source_slug>/edit/", views.source_edit, name="source_edit"),
    path("<slug:collection_slug>/<slug:source_slug>/transcribe/", views.start_transcription, name="start_transcription"),
    path("<slug:collection_slug>/<slug:source_slug>/export/<str:kind>/", views.source_export, name="source_export"),
    path("<slug:collection_slug>/<slug:source_slug>/event/", views.source_event_api, name="source_event_api"),
]
