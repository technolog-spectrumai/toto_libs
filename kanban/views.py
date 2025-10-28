from django.views.generic import DetailView, CreateView, UpdateView, ListView
from django.db.models import Q
from kanban.models import Board, Column, Task
from kanban.page import PageProcessor
from kanban.forms import TaskForm
from oya.models import Theme
from .forms import TaskForm
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.mixins import LoginRequiredMixin



class BoardDetailView(LoginRequiredMixin, DetailView):
    model = Board
    template_name = 'kanban/board.html'
    context_object_name = 'board'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        board = self.get_object()
        context['columns'] = board.column_set.order_by('position').prefetch_related('task_set')
        context['form'] = TaskForm()  # Add this line
        return context


class BoardListView(LoginRequiredMixin, ListView):
    model = Board
    template_name = 'kanban/board_list.html'
    context_object_name = 'boards'

    def get_queryset(self):
        user = self.request.user
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return Board.objects.none()  # Return empty queryset for anonymous users

        return Board.objects.filter(
            Q(project__owner=user) |
            Q(project__collaborators=user)
        ).distinct().select_related('project')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = PageProcessor()
        context = page.decorate(context, self.request)

        return context

