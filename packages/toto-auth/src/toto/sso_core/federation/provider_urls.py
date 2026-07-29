"""The url tree a provider host mounts.

Same shape as consumer_urls, resolved in the other mode. Note the include
shapes genuinely differ: sso_master.urls carries its own ``sso/`` and
``.well-known/`` prefixes and is mounted at the root, while sso_client.urls is
mounted under ``sso/``. auth_urlpatterns owns that difference, which is why
both trees are built through it.
"""
from django.contrib import admin
from django.urls import include, path

from toto.auth_config import MODE_PROVIDER, auth_urlpatterns, resolve_auth

_CFG = resolve_auth({"TOTO_AUTH_MODE": MODE_PROVIDER}.get)

urlpatterns = [
    path("admin/", admin.site.urls),
    # The shared chrome reverses set_language; the consent page extends it.
    path("i18n/", include("django.conf.urls.i18n")),
    path("core/", include("toto.core.urls", namespace="core")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    *auth_urlpatterns(_CFG),
]
