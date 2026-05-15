from urllib.parse import urlencode

from django.db import models
from django.views.generic import ListView

from .models import MemoCard, MemoDeck, Tag
from toto.core.page import PageProcessor


class MemoDeckListView(ListView):
    model = MemoDeck
    template_name = 'memo/deck_list.html'
    context_object_name = 'decks'
    paginate_by = 8

    def get_queryset(self):
        queryset = (
            MemoDeck.objects
            .select_related("author")
            .prefetch_related("tags", "cards")
            .order_by("-created_at", "title")
        )

        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                models.Q(title__icontains=query) |
                models.Q(description__icontains=query) |
                models.Q(tags__name__icontains=query)
            ).distinct()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "").strip()
        context["tags"] = Tag.objects.all().order_by("name")
        context["querystring"] = f"{urlencode({'q': context['query']})}&" if context["query"] else ""
        return PageProcessor().decorate(context, self.request)


class MemoCardListView(ListView):
    model = MemoCard
    template_name = 'memo/card_list.html'
    context_object_name = 'cards'

    def get_queryset(self):
        deck = MemoDeck.objects.get(slug=self.kwargs['slug'])
        return MemoCard.objects.filter(deck=deck).order_by('order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deck = MemoDeck.objects.get(slug=self.kwargs['slug'])
        context['deck'] = deck
        return PageProcessor().decorate(context, self.request)
