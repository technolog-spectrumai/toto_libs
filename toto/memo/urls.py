from django.urls import path

from .views import (
    PresentationIndexView,
    PresentationView,
    PresentationEditView,
    PresentationCreateView,
    PresentationSourceView,
    presentation_save,
    presentation_source_save,
    presentation_media_embed,
)

app_name = 'memo'

urlpatterns = [
    path('', PresentationIndexView.as_view(), name='index'),
    path('new/', PresentationCreateView.as_view(), name='create'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('edit/<int:file_pk>/', PresentationEditView.as_view(), name='edit'),
    path('save/<int:file_pk>/', presentation_save, name='save'),
    path('source/<int:file_pk>/', PresentationSourceView.as_view(), name='source'),
    path('source/<int:file_pk>/save/', presentation_source_save, name='source_save'),
    path('media/embed/', presentation_media_embed, name='media_embed'),
]
