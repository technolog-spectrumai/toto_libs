from django.urls import path
from .views import MemoDeckListView, MemoCardListView, MemoCardDetailView
from django.views.generic.base import RedirectView
from django.conf import settings

urlpatterns = [
    path('', RedirectView.as_view(url='decks/', permanent=not settings.DEBUG)),
    path('decks/', MemoDeckListView.as_view(), name='deck-list'),
    path('deck/<int:deck_id>/cards/', MemoCardListView.as_view(), name='card-list'),
    path('card/<int:pk>/', MemoCardDetailView.as_view(), name='card-detail'),
]
