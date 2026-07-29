"""The url tree a consumer host mounts.

Built through ``auth_urlpatterns`` rather than by naming ``sso_client.urls``
directly, so the suite also proves the host-facing API that studio's urls.py
calls — not just the views underneath it.
"""
from django.contrib import admin
from django.urls import include, path

from toto.auth_config import MODE_CONSUMER, auth_urlpatterns, resolve_auth

_CFG = resolve_auth({"TOTO_AUTH_MODE": MODE_CONSUMER}.get)

urlpatterns = [
    path("admin/", admin.site.urls),
    # The shared chrome reverses set_language; a host without this 500s every
    # page that extends oya/base.html.
    path("i18n/", include("django.conf.urls.i18n")),
    path("core/", include("toto.core.urls", namespace="core")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    *auth_urlpatterns(_CFG),
]
