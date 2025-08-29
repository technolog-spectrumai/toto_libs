from django.views.generic import DetailView, CreateView, UpdateView
from kanban.models import Board, Column, Task
from kanban.page import PageProcessor
from django.urls import reverse
from kanban.forms import TaskForm
from oya.models import Theme
from django.views.generic.edit import FormView
from django.shortcuts import get_object_or_404, render, redirect
from .models import Column
from .forms import TaskForm
from django.views.generic.edit import DeleteView
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.http import JsonResponse
import json


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

    def form_valid(self, form):
        column = get_object_or_404(Column, pk=self.kwargs["column_pk"])
        task = form.save(commit=False)
        task.column = column
        task.save()
        return super().form_valid(form)

    def get_success_url(self):
        column = get_object_or_404(Column, pk=self.kwargs["column_pk"])
        return reverse("kanban:board", kwargs={"pk": column.board.pk})


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


class TaskDeleteView(DeleteView):
    model = Task
    template_name = "kanban/confirm_delete_task.html"
    pk_url_kwarg = "task_pk"

    def get_success_url(self):
        task = self.get_object()
        return reverse_lazy("kanban:board", kwargs={"board_pk": task.column.board.pk})

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

def confirm_delete_task(request, board_pk, task_pk):
    board = get_object_or_404(Board, pk=board_pk)
    task = get_object_or_404(Task, pk=task_pk)
    context = {
        'board': board,
        'task': task,
    }
    context = PageProcessor().decorate(context, request)
    if request.method == "POST":
        task.delete()
        return redirect('kanban:board', pk=board.pk)
    return render(request, 'kanban/confirm_delete.html', context)


@require_POST
def move_task(request, task_id):
    try:
        data = json.loads(request.body)
        new_column_id = data.get("column_id")
        if not new_column_id:
            return JsonResponse({"error": "Missing column_id"}, status=400)

        new_order = data.get("order")
        print("--->", new_order)
        if new_order is None:
            return JsonResponse({"error": "Missing order"}, status=400)

        task = get_object_or_404(Task, pk=task_id)
        new_column = get_object_or_404(Column, pk=new_column_id)

        task.column = new_column
        # Shift other tasks in the column
        siblings = Task.objects.filter(column=new_column).exclude(pk=task.pk).order_by("position")
        new_order = int(new_order)
        for i, sibling in enumerate(siblings):
            sibling.position = i if i < new_order else i + 1
            sibling.save()
        task.position = new_order
        task.save()

        return JsonResponse({
            "status": "success",
            "task_id": task.id,
            "new_column_id": new_column.id
        })

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
