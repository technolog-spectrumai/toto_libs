"""The url tree the memo suite drives — mounted the way zenobia mounts it."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # oya/header.html reverses sso:login and sso:logout on every page.
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    # The vault, for the Play/Edit plugin routing tests.
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("editor/", include("toto.editor.urls", namespace="editor")),
    path("memo/", include("toto.memo.urls", namespace="memo")),
]
