"""The url tree the text recognition suite drives — mounted the way a host
mounts it (`/ocr/`)."""
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
    path("ocr/", include("toto.ocr.urls", namespace="ocr")),
]
