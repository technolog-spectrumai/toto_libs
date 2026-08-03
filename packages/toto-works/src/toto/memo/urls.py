from django.urls import path

from .views import (
    PresentationIndexView,
    PresentationView,
    PresentationEditView,
    PresentationCreateView,
    PresentationImportView,
    presentation_save,
    presentation_media_embed,
    presentation_media_upload,
    presentation_export_pdf,
    presentation_export_zip,
)

app_name = 'memo'

urlpatterns = [
    path('', PresentationIndexView.as_view(), name='index'),
    path('new/', PresentationCreateView.as_view(), name='create'),
    path('import/', PresentationImportView.as_view(), name='import'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('edit/<int:file_pk>/', PresentationEditView.as_view(), name='edit'),
    path('save/<int:file_pk>/', presentation_save, name='save'),
    path('export/<int:file_pk>/pdf/', presentation_export_pdf, name='export_pdf'),
    path('export/<int:file_pk>/zip/', presentation_export_zip, name='export_zip'),
    path('media/embed/', presentation_media_embed, name='media_embed'),
    path('media/upload/', presentation_media_upload, name='media_upload'),
]
