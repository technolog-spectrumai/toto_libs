"""The writer's routes.

Only what an owning app drives. Cyprian stopped being a destination when the
editors moved to the zinnia desktop app: there is no library index, no reader
and no export here any more. What remains is the surface kanban's wiki opens —
see bridge.py — and the media endpoints that surface needs.
"""
from django.urls import path

from . import views

app_name = "cyprian"

urlpatterns = [
    path("edit/<int:file_pk>/", views.DocumentEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", views.document_save, name="save"),
    path("source/<int:file_pk>/", views.document_source, name="source"),
    # The two conversions. They live here because cyprian owns the CTML
    # format; they are POST-only and each creates a NEW file, which is also
    # what lets toto.htmlview keep its "nowhere to write" guarantee.
    path("html-to-ctml/<int:file_pk>/", views.html_to_ctml, name="html_to_ctml"),
    path("ctml-to-html/<int:file_pk>/", views.ctml_to_html, name="ctml_to_html"),
    path("file/<int:file_pk>/", views.rendition, name="rendition"),

    path("media/embed/", views.document_media_embed, name="media_embed"),
    path("media/upload/", views.document_media_upload, name="media_upload"),
]
