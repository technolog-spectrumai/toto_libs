"""The url tree the kanban suite drives — mounted the way zenobia mounts it."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # oya/header.html reverses sso:login and sso:logout on every page.
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("locations/", include("toto.locations.urls", namespace="locations")),
    path("events/", include("toto.events.urls", namespace="events")),
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("editor/", include("toto.editor.urls", namespace="editor")),
    path("memo/", include("toto.memo.urls", namespace="memo")),
    # The writer, for the bridge tests: a page's prose opens at cyprian:edit.
    path("cyprian/", include("toto.cyprian.urls", namespace="cyprian")),
    path("kanban/", include("toto.kanban.urls", namespace="kanban")),
]
