"""The writer's routes.

Only what an owning app drives, and since 2026-08-29 there is exactly one:
kanban's wiki. Cyprian stopped being a destination when the editors moved to
the zinnia desktop app, and it stopped owning a FILE TYPE when CTML was
retired — it claims nothing in the vault and offers no conversion. What
remains is the rich-text surface a wiki page opens (see
bridge.py) and the media endpoints that surface needs.
"""
from django.urls import path

from . import views

app_name = "cyprian"

urlpatterns = [
    path("edit/<int:file_pk>/", views.DocumentEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", views.document_save, name="save"),
    path("source/<int:file_pk>/", views.document_source, name="source"),
    # No conversion routes. CTML was retired on 2026-08-29 and a document is
    # an HTML page now, so there is nothing to convert between — see views.py.
    path("file/<int:file_pk>/", views.rendition, name="rendition"),

    path("media/embed/", views.document_media_embed, name="media_embed"),
    path("media/upload/", views.document_media_upload, name="media_upload"),
]
