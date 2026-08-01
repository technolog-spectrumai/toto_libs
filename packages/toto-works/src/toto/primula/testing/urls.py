"""The url tree the Primula suite drives — mounted the way zenobia would mount it."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # oya/header.html reverses sso:login and sso:logout on every page.
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    # The vault, for the editor-plugin routing test (its file list links "open").
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("editor/", include("toto.editor.urls", namespace="editor")),
    path("primula/", include("toto.primula.urls", namespace="primula")),
]
