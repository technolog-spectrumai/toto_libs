from django.views.generic import ListView, DetailView
from .models import MemoCard, MemoDeck
from .page import PageProcessor


class MemoDeckListView(ListView):
    model = MemoDeck
    template_name = 'memo/deck_list.html'
    context_object_name = 'decks'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class MemoCardListView(ListView):
    model = MemoCard
    template_name = 'memo/card_list.html'
    context_object_name = 'cards'

    def get_queryset(self):
        deck_id = self.kwargs['deck_id']
        return MemoCard.objects.filter(deck_id=deck_id).order_by('order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deck = MemoDeck.objects.get(pk=self.kwargs['deck_id'])
        context['deck'] = deck
        return PageProcessor().decorate(context, self.request)
