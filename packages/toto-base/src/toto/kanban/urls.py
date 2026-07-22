from django.urls import path
from .api_views import (
    ProjectListApiView,
    ProjectDetailApiView,
    TaskListCreateApiView,
    TaskDetailApiView,
    TaskPromoteApiView,
    TaskDemoteApiView,
    ProjectMissionsApiView,
    MissionDetailApiView,
    SprintMetricsApiView,
    BacklogApiView,
    EisenhowerMatrixApiView,
)
from .views import (
    ProjectListView,
    ProjectDetailView,
    EisenhowerMatrixView,
    BacklogView,
    MissionDetailView,
    TaskCreateView,
    TaskUpdateView,
    TaskDeleteView,
    promote_task,
    demote_task,
    SprintMetricsView,
    DocumentationPageDetailView,
)

app_name = "kanban"

urlpatterns = [
    # Enigma JSON API
    path("api/projects/", ProjectListApiView.as_view(), name="api_project_list"),
    path("api/projects/<int:pk>/", ProjectDetailApiView.as_view(), name="api_project_detail"),
    path("api/projects/<int:project_pk>/tasks/", TaskListCreateApiView.as_view(), name="api_task_list"),
    path("api/tasks/<int:pk>/", TaskDetailApiView.as_view(), name="api_task_detail"),
    path("api/tasks/<int:pk>/promote/", TaskPromoteApiView.as_view(), name="api_task_promote"),
    path("api/tasks/<int:pk>/demote/", TaskDemoteApiView.as_view(), name="api_task_demote"),
    path("api/projects/<int:pk>/missions/", ProjectMissionsApiView.as_view(), name="api_project_missions"),
    path("api/projects/<int:pk>/backlog/", BacklogApiView.as_view(), name="api_project_backlog"),
    path("api/projects/<int:pk>/sprint-metrics/", SprintMetricsApiView.as_view(), name="api_sprint_metrics"),
    path("api/projects/<int:pk>/matrix/", EisenhowerMatrixApiView.as_view(), name="api_eisenhower_matrix"),
    path("api/missions/<int:pk>/", MissionDetailApiView.as_view(), name="api_mission_detail"),

    path("", ProjectListView.as_view(), name="project_list"),

    path(
        "project/<int:pk>/",
        ProjectDetailView.as_view(),
        name="project_detail",
    ),

    path(
        "project/<int:pk>/matrix/",
        EisenhowerMatrixView.as_view(),
        name="eisenhower_matrix",
    ),

    path(
        "project/<int:pk>/backlog/",
        BacklogView.as_view(),
        name="backlog",
    ),

    path(
        "mission/<int:pk>/",
        MissionDetailView.as_view(),
        name="mission_detail",
    ),

    path(
        "documentation/<int:pk>/",
        DocumentationPageDetailView.as_view(),
        name="documentation_page_detail",
    ),

    path(
        "project/<int:pk>/task/new/",
        TaskCreateView.as_view(),
        name="task_create",
    ),

    path(
        "project/<int:project_pk>/task/<int:pk>/edit/",
        TaskUpdateView.as_view(),
        name="task_edit",
    ),

    path(
        "project/<int:project_pk>/task/<int:pk>/delete/",
        TaskDeleteView.as_view(),
        name="task_delete",
    ),

    path(
        "<int:project_id>/task/<int:task_id>/promote/",
        promote_task,
        name="task_promote",
    ),

    path(
        "<int:project_id>/task/<int:task_id>/demote/",
        demote_task,
        name="task_demote",
    ),

    path(
        "projects/<int:pk>/sprint-metrics/",
        SprintMetricsView.as_view(),
        name="sprint_metrics",
    ),

]
