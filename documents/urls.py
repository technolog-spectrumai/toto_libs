from django.conf import settings
from django.urls import path
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from .views import (
    DocumentListView,
    DocumentDetailView,
    SectionDetailView,
    # OfficeListView, OfficeDetailView  # Uncomment when needed
)

urlpatterns = [
    path('', RedirectView.as_view(
        url=reverse_lazy('document-list'),
        permanent=not settings.DEBUG
    ), name='index'),

    path('documents/', DocumentListView.as_view(), name='document-list'),
    path('documents/<slug:slug>/', DocumentDetailView.as_view(), name='document-detail'),

    path('sections/<int:pk>/', SectionDetailView.as_view(), name='section-detail'),

    # path('offices/', OfficeListView.as_view(), name='office-list'),
    # path('offices/<int:pk>/', OfficeDetailView.as_view(), name='office-detail'),
]
