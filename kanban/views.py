from django.views.generic import DetailView, CreateView, UpdateView
from django.shortcuts import get_object_or_404
from kanban.models import Board, Column, Task
from kanban.page import PageProcessor
from django.urls import reverse
from kanban.forms import TaskForm
from oya.models import Theme


class BoardDetailView(DetailView):
    model = Board
    template_name = 'kanban/board.html'
    context_object_name = 'board'

    # def get_context_data(self, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     PageProcessor().decorate(context, self.request)
    #     board = self.get_object()
    #     columns = board.column_set.order_by('position').prefetch_related('task_set')
    #     context['columns'] = columns
    #     return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        board = self.get_object()
        context['columns'] = board.column_set.order_by('position').prefetch_related('task_set')
        context['form'] = TaskForm()  # Add this line
        return context


class TaskCreateView(CreateView):
    model = Task
    form_class = TaskForm
    template_name = 'kanban/task_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.column = get_object_or_404(Column, pk=kwargs['column_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Load your theme (e.g., default or user-specific)
        theme = Theme.objects.first()  # or customize this logic
        kwargs["theme"] = theme
        return kwargs

    def form_valid(self, form):
        form.instance.column = self.column
        return super().form_valid(form)

    def get_success_url(self):
        board_url = self.column.board.get_absolute_url()
        return f"{board_url}#column-{self.column.pk}"



class TaskUpdateView(UpdateView):
    model = Task
    fields = ['title', 'description']
    template_name = 'kanban/task_form.html'
    pk_url_kwarg = 'task_pk'

    def get_success_url(self):
        # After edit, go back to the board, anchored on the same column
        column = self.object.column
        board_url = column.board.get_absolute_url()
        return f"{board_url}#column-{column.pk}"
