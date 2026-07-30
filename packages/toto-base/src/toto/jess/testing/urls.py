"""The url tree the Jess suite drives.

``toto.core.urls`` is mounted at the root because a host mounts it there (the studio
settings review found that out the hard way) and because ``core/login/`` is where the
"Forgot password?" link either appears or does not — which is the visible consequence of
``email_delivery_configured()``, so the suite must be able to load that page.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # The `sso` namespace, mounted the way zenobia mounts it (zenobia/urls.py:18).
    # oya/header.html reverses sso:login and sso:logout on every page, so without this
    # every view test fails on a NoReverseMatch that has nothing to do with Jess.
    path("", include("toto.sso_master.urls", namespace="sso")),
    # The login page reverses socialhub: for its "apply for membership" route. Mounting
    # it makes this tree provider-shaped enough to also host
    # ``sso_master.tests.test_password_reset`` — which is the suite Jess must not
    # regress, and which no gate currently runs.
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("jess/", include("toto.jess.urls", namespace="jess")),
]
