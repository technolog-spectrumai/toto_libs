from django.urls import path

from . import views

app_name = "cyprian"

urlpatterns = [
    path("", views.DocumentIndexView.as_view(), name="index"),
    path("new/", views.DocumentCreateView.as_view(), name="create"),

    path("read/<int:file_pk>/", views.DocumentReadView.as_view(), name="read"),
    path("edit/<int:file_pk>/", views.DocumentEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", views.document_save, name="save"),
    path("source/<int:file_pk>/", views.document_source, name="source"),
    path("export/<int:file_pk>/pdf/", views.document_export_pdf, name="export_pdf"),
    path("save/<int:file_pk>/pdf/", views.document_save_pdf, name="save_pdf"),
    path("save/<int:file_pk>/html/", views.document_save_html, name="save_html"),
    path("file/<int:file_pk>/", views.rendition, name="rendition"),
    path("contract/<int:file_pk>/", views.from_contract, name="from_contract"),

    path("media/embed/", views.document_media_embed, name="media_embed"),
    path("media/upload/", views.document_media_upload, name="media_upload"),
]
