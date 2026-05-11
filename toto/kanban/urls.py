from django.urls import path
from .views import (
    ProjectListView,
    ProjectDetailView,
    EisenhowerMatrixView,
    BacklogView,
    TaskCreateView, TaskUpdateView, TaskDeleteView, promote_task, demote_task, sprint_metrics
)

app_name = 'kanban'

urlpatterns = [
    path('', ProjectListView.as_view(), name='project_list'),

    path('project/<int:pk>/',
         ProjectDetailView.as_view(),
         name='project_detail'),

    path('project/<int:pk>/matrix/',
         EisenhowerMatrixView.as_view(),
         name='eisenhower_matrix'),

    path('project/<int:pk>/backlog/',
         BacklogView.as_view(),
         name='backlog'),

    # ⭐ NEW: Create Task
    path('project/<int:pk>/task/new/',
         TaskCreateView.as_view(),
         name='task_create'),
    path(
        "project/<int:project_pk>/task/<int:pk>/edit/",
        TaskUpdateView.as_view(),
        name="task_edit",
    ),
    path(
        "project/<int:project_pk>/task/<int:pk>/delete/",
        TaskDeleteView.as_view(),
        name="task_delete"),
    path('<int:project_id>/task/<int:task_id>/promote/', promote_task, name='task_promote'),
    path('<int:project_id>/task/<int:task_id>/demote/', demote_task, name='task_demote'),
    path(
        "projects/<int:pk>/sprint-metrics/",
        sprint_metrics,
        name="sprint_metrics",
    ),
]
