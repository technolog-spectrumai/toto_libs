"""The url tree the knowledge graph suite drives — mounted the way a host
mounts it (`/ravioli/`, `/neo-editor/`)."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # oya/header.html reverses sso:login and sso:logout on every page.
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("ravioli/", include("toto.ravioli.urls", namespace="ravioli")),
    # The NeoJSON export answers with the editor's url.
    path("neo-editor/", include("toto.neo_editor.urls", namespace="neo_editor")),
]
