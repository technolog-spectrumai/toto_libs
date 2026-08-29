"""The url tree the Hesperis suite drives — mounted the way zenobia mounts it."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("locations/", include("toto.locations.urls", namespace="locations")),
    path("events/", include("toto.events.urls", namespace="events")),
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("editor/", include("toto.editor.urls", namespace="editor")),
    path("memo/", include("toto.memo.urls", namespace="memo")),
    path("cyprian/", include("toto.cyprian.urls", namespace="cyprian")),
    path("kanban/", include("toto.kanban.urls", namespace="kanban")),
    path("hesperis/", include("toto.hesperis.urls", namespace="hesperis")),
]
