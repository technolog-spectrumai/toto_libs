"""The shared workspace routes, as a factory.

There is no `ambrosia` namespace any more: the base app mounts nothing itself.
`toto.dracena` and `toto.texlab` each embed these patterns under their own
namespace and append their language's endpoints — which is why every view here
reverses through the request's live namespace rather than a literal.
"""

from django.urls import path

from . import views


def workspace_urlpatterns():
    return [
        path("", views.lobby, name="lobby"),
        path("new/", views.workspace_create, name="workspace_create"),
        path("w/<slug:slug>/", views.workspace, name="workspace"),
        path("w/<slug:slug>/settings/", views.workspace_settings, name="workspace_settings"),
        path("w/<slug:slug>/close/", views.workspace_close, name="workspace_close"),
        path("w/<slug:slug>/destroy/", views.workspace_destroy, name="workspace_destroy"),
        path("w/<slug:slug>/hibernate/", views.workspace_hibernate, name="workspace_hibernate"),
        path("w/<slug:slug>/wake/", views.workspace_rehydrate, name="workspace_rehydrate"),
        path("w/<slug:slug>/tree/", views.tree, name="tree"),
        path("w/<slug:slug>/files/new/", views.file_create, name="file_create"),
        path("w/<slug:slug>/folders/new/", views.dir_create, name="dir_create"),
        path("w/<slug:slug>/files/<int:pk>/", views.file_content, name="file_content"),
        path("w/<slug:slug>/files/<int:pk>/save/", views.file_save, name="file_save"),
        path("w/<slug:slug>/files/<int:pk>/rename/", views.file_rename, name="file_rename"),
        path("w/<slug:slug>/files/<int:pk>/delete/", views.file_delete, name="file_delete"),
        path("w/<slug:slug>/files/<int:pk>/raw/", views.file_raw, name="file_raw"),
    ]
