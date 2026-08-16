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
    WikiIndexView,
    WikiPageDetailView,
    WikiPageCreateView,
    WikiPageUpdateView,
    WikiPageDeleteView,
    wiki_page_write,
    WikiSearchView,
    documentation_page_redirect,
    ProjectDetailView,
    EisenhowerMatrixView,
    BacklogView,
    MissionDetailView,
    TaskCreateView,
    TaskUpdateView,
    TaskDeleteView,
    promote_task,
    demote_task,
    relation_create,
    relation_delete,
    MissionCreateView,
    MissionUpdateView,
    mission_event_link,
    mission_event_create,
    task_event_link,
    task_event_create,
    mission_attachment_add,
    mission_attachment_remove,
    CampaignMapView,
    CampaignCalendarView,
    campaign_map_data,
    SprintMetricsView,
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

    # ── The wiki ────────────────────────────────────────────────────────────
    # Across every project you belong to. Deliberately NOT under project/<pk>/:
    # it is the door you use when you do not know which board a page is on,
    # which is the ordinary case for a wiki.
    path("wiki/", WikiSearchView.as_view(), name="wiki_search"),
    # A project is the space; `slug` is unique within it, which is why these are
    # keyed on slug rather than pk — a wiki URL should be readable and should
    # survive being pasted into a page.
    path(
        "project/<int:pk>/wiki/",
        WikiIndexView.as_view(),
        name="wiki_index",
    ),
    path(
        "project/<int:pk>/wiki/new/",
        WikiPageCreateView.as_view(),
        name="wiki_page_create",
    ),
    path(
        "project/<int:pk>/wiki/<slug:slug>/",
        WikiPageDetailView.as_view(),
        name="wiki_page",
    ),
    path(
        "project/<int:pk>/wiki/<slug:slug>/edit/",
        WikiPageUpdateView.as_view(),
        name="wiki_page_edit",
    ),
    path(
        "project/<int:pk>/wiki/<slug:slug>/delete/",
        WikiPageDeleteView.as_view(),
        name="wiki_page_delete",
    ),
    path(
        "project/<int:pk>/wiki/<slug:slug>/write/",
        wiki_page_write,
        name="wiki_page_write",
    ),

    # The old per-mission documentation URL, kept as a redirect. Django admin's
    # "View on site" reverses get_absolute_url, and pages have been linked from
    # mission pages for a year — a 404 here would be a self-inflicted wound.
    path(
        "documentation/<int:pk>/",
        documentation_page_redirect,
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
        "<int:project_id>/task/<int:task_id>/relation/new/",
        relation_create,
        name="relation_create",
    ),

    path(
        "<int:project_id>/relation/<int:pk>/delete/",
        relation_delete,
        name="relation_delete",
    ),

    path(
        "projects/<int:pk>/sprint-metrics/",
        SprintMetricsView.as_view(),
        name="sprint_metrics",
    ),

    path(
        "project/<int:pk>/mission/new/",
        MissionCreateView.as_view(),
        name="mission_create",
    ),

    path(
        "project/<int:project_pk>/mission/<int:pk>/edit/",
        MissionUpdateView.as_view(),
        name="mission_edit",
    ),


    path(
        "mission/<int:pk>/event/link/",
        mission_event_link,
        name="mission_event_link",
    ),

    path(
        "mission/<int:pk>/event/create/",
        mission_event_create,
        name="mission_event_create",
    ),

    path(
        "<int:project_id>/task/<int:task_id>/event/link/",
        task_event_link,
        name="task_event_link",
    ),

    path(
        "<int:project_id>/task/<int:task_id>/event/create/",
        task_event_create,
        name="task_event_create",
    ),

    path(
        "mission/<int:pk>/attachments/add/",
        mission_attachment_add,
        name="mission_attachment_add",
    ),

    path(
        "mission/<int:mission_pk>/attachments/<int:pk>/remove/",
        mission_attachment_remove,
        name="mission_attachment_remove",
    ),

    path(
        "campaign/<int:pk>/map/",
        CampaignMapView.as_view(),
        name="campaign_map",
    ),

    path(
        "campaign/<int:pk>/map/data/",
        campaign_map_data,
        name="campaign_map_data",
    ),

    path(
        "campaign/<int:pk>/calendar/",
        CampaignCalendarView.as_view(),
        name="campaign_calendar",
    ),

]
