"""A URLconf for the desk's own tests.

The app is mounted by whichever host installs it, so its tests cannot assume a
prefix. This gives them one.
"""

from django.apps import apps
from django.urls import include, path

# oya/header.html reverses sso:login and sso:logout on every page, and the desk
# renders the header now that index() goes through PageProcessor (a decorated
# context has `platform`, which is what the header is gated on). Before that the
# tests passed WITHOUT an sso mount — by accident: the missing decoration
# suppressed the header, which is the very bug being fixed.
#
# WHICH SIDE, though. This named toto.sso_master outright until 2026-09-01, and
# that was a provider assumption nothing caught while the only host installing
# anastasia WAS the provider. The first federation CONSUMER to install this app
# could not import the harness at all: a consumer mounts toto.sso_client and no
# toto.sso_master exists to include. Both halves expose `login` and `logout`
# under the `sso` namespace, which is all the header reverses — so ask the app
# registry rather than assuming, exactly as a real host's auth_urlpatterns does.
_SSO_URLS = ("toto.sso_master.urls" if apps.is_installed("toto.sso_master")
             else "toto.sso_client.urls")

urlpatterns = [
    path("capsules/", include("toto.anastasia.urls", namespace="anastasia")),
    path("", include(_SSO_URLS, namespace="sso")),
]
