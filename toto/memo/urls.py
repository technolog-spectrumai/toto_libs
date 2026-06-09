from django.urls import path

from .views import (
    PresentationIndexView,
    PresentationView,
    PresentationEditView,
    PresentationCreateView,
    presentation_save,
)

app_name = 'memo'

urlpatterns = [
    path('', PresentationIndexView.as_view(), name='index'),
    path('new/', PresentationCreateView.as_view(), name='create'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('edit/<int:file_pk>/', PresentationEditView.as_view(), name='edit'),
    path('save/<int:file_pk>/', presentation_save, name='save'),
]
