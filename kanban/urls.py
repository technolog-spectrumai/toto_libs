from django.urls import path
from .views import BoardDetailView, TaskCreateView, TaskEditView, TaskDeleteView

app_name = 'kanban'

urlpatterns = [
    path('board/<int:pk>/', BoardDetailView.as_view(), name='board'),

    # Create a new task under column with pk=column_pk
    path(
        'column/<int:column_pk>/task/add/',
        TaskCreateView.as_view(),
        name='add_task'
    ),

    # Update an existing task with pk=task_pk
    path(
        'task/<int:task_pk>/edit/',
        TaskEditView.as_view(),
        name='edit_task'
    ),
    path("task/<int:task_pk>/delete/", TaskDeleteView.as_view(), name="delete_task")
]
