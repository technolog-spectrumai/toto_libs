"""Routes for the workspace API.

Mounted at ``/api/v1/`` beside anastasia's, under its own namespace. The two
share a version number on purpose: one client, one number. A desktop app that
had to track ``/api/v1/capsules`` against ``/api/v2/workspaces`` would
eventually be built against a pair nobody tested together.

When a shape must break, ``api_urls_v2.py`` appears beside this and this file
is left alone.

ORDER. Every fixed segment is declared before the pattern that could swallow
it. There is no ``<str:action>`` catch-all here, deliberately — anastasia had
one and it silently shadowed ``/storage``, which came back 405 and read like a
broken endpoint rather than a shadowed route. The verbs here are named.
"""

from django.urls import path

from . import api

app_name = "ambrosia_api"

urlpatterns = [
    path("workspaces", api.workspace_list, name="workspace_list"),
    path("workspaces/<slug:slug>", api.workspace_detail,
         name="workspace_detail"),
    path("workspaces/<slug:slug>/tree", api.workspace_tree,
         name="workspace_tree"),
    path("workspaces/<slug:slug>/run", api.workspace_run, name="workspace_run"),

    # Files. `files/new` and `folders/new` before `files/<int:pk>` — the int
    # converter would refuse "new" anyway, but the next person to reach for a
    # `<str:>` converter should not have to rediscover why that matters.
    path("workspaces/<slug:slug>/files/new", api.file_create,
         name="file_create"),
    path("workspaces/<slug:slug>/folders/new", api.dir_create,
         name="dir_create"),
    path("workspaces/<slug:slug>/files/<int:pk>", api.file_read,
         name="file_read"),
    path("workspaces/<slug:slug>/files/<int:pk>/save", api.file_write,
         name="file_write"),
    path("workspaces/<slug:slug>/files/<int:pk>/rename", api.file_rename,
         name="file_rename"),
    path("workspaces/<slug:slug>/files/<int:pk>/delete", api.file_delete,
         name="file_delete"),
]
