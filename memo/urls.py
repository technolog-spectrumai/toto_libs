from django.urls import path
from .views import MemoDeckListView, MemoCardListView, MemoCardDetailView

urlpatterns = [
    path('decks/', MemoDeckListView.as_view(), name='deck-list'),
    path('deck/<int:deck_id>/cards/', MemoCardListView.as_view(), name='card-list'),
    path('card/<int:pk>/', MemoCardDetailView.as_view(), name='card-detail'),
]
