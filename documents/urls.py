from django.conf import settings
from django.urls import path
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from .views import (
    DocumentListView,
    DocumentDetailView,
    document_pdf_view
)

urlpatterns = [
    path('', RedirectView.as_view(
        url=reverse_lazy('document-list'),
        permanent=not settings.DEBUG
    ), name='index'),

    path('documents/', DocumentListView.as_view(), name='document-list'),
    path('documents/<slug:slug>/', DocumentDetailView.as_view(), name='document-detail'),
    path('export/pdf/<slug:slug>/', document_pdf_view, name='document-pdf'),
]
