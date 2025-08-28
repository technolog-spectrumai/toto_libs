from django.views.generic import DetailView, CreateView, UpdateView
from django.shortcuts import get_object_or_404
from kanban.models import Board, Column, Task
from kanban.page import PageProcessor
from django.urls import reverse
from kanban.forms import TaskForm
from oya.models import Theme
# views.py
from django.views.generic.edit import FormView
from django.shortcuts import get_object_or_404, render
from .models import Column
from .forms import TaskForm


class BoardDetailView(DetailView):
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


class TaskCreateView(FormView):
    form_class = TaskForm
    template_name = "kanban/edit_task.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        column_id = self.kwargs["column_pk"]
        column = get_object_or_404(Column, pk=column_id)
        board = column.board
        context.update({
            "column": column,
            "board": board,
        })
        return context


class TaskEditView(UpdateView):
    model = Task
    form_class = TaskForm
    template_name = "kanban/edit_task.html"
    pk_url_kwarg = "task_pk"  # assuming your URL uses task_pk

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        task = self.get_object()
        column = task.column
        board = column.board

        context.update({
            "task": task,
            "column": column,
            "board": board,
        })
        return context

