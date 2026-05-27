from django.urls import path

from . import views

app_name = "vod"

urlpatterns = [
    path("", views.collection_list, name="collection_list"),
    path("collections/new/", views.collection_create, name="collection_create"),
    path("upload/", views.upload, name="upload"),
    path("<slug:slug>/", views.collection_detail, name="collection_detail"),
    path("<slug:collection_slug>/<slug:video_slug>/", views.video_detail, name="video_detail"),
    path("<slug:collection_slug>/<slug:video_slug>/edit/", views.video_edit, name="video_edit"),
    path("<slug:collection_slug>/<slug:video_slug>/build-hls/", views.build_hls, name="build_hls"),
    path("<slug:collection_slug>/<slug:video_slug>/invoice-access/", views.request_invoice_access, name="request_invoice_access"),
    path("<slug:collection_slug>/<slug:video_slug>/event/", views.playback_event_api, name="playback_event_api"),
]
