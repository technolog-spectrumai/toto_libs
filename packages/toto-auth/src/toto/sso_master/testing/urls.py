"""A provider-shaped url tree.

The `sso` namespace resolves to sso_master here, which is the whole point: under
the federation harness it resolves to sso_client, so `sso:login` is a federated
redirect and the provider's own login tests fail against correct code.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # toto.api at "api/": test_register_api exchanges its session key at the
    # literal path /api/me/, so the prefix is part of the contract.
    path("api/", include("toto.api.urls")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("", include("toto.sso_master.urls", namespace="sso")),
]
