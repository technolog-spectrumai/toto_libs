from django.views.generic import DetailView
from django.shortcuts import get_object_or_404
from .models import Board
from .page import PageProcessor

class BoardDetailView(DetailView):
    model = Board
    template_name = 'kanban/board.html'
    context_object_name = 'board'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        board = self.get_object()
        columns = board.column_set.order_by('position').prefetch_related('task_set')
        context['columns'] = columns
        return context