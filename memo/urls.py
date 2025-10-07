from django.urls import path
from .views import MemoDeckListView, MemoCardListView
from django.views.generic.base import RedirectView
from django.conf import settings


app_name = 'memo'

urlpatterns = [
    path('', RedirectView.as_view(url='decks/', permanent=not settings.DEBUG)),
    path('decks/', MemoDeckListView.as_view(), name='deck-list'),
    path('deck/<int:deck_id>/cards/', MemoCardListView.as_view(), name='card-list')
]
