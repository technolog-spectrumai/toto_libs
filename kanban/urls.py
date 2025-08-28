from django.urls import path
from .views import BoardDetailView, TaskCreateView, TaskUpdateView

app_name = 'kanban'

urlpatterns = [
    path('board/<int:pk>/', BoardDetailView.as_view(), name='board-detail'),

    # Create a new task under column with pk=column_pk
    path(
        'column/<int:column_pk>/task/add/',
        TaskCreateView.as_view(),
        name='kanban-task-add'
    ),

    # Update an existing task with pk=task_pk
    path(
        'task/<int:task_pk>/edit/',
        TaskUpdateView.as_view(),
        name='kanban-task-edit'
    ),
]
