"""A URLconf for the desk's own tests.

The app is mounted by whichever host installs it, so its tests cannot assume a
prefix. This gives them one.
"""

from django.urls import include, path

urlpatterns = [
    path("gears/", include("toto.anastasia.urls", namespace="anastasia")),
    # oya/header.html reverses sso:login and sso:logout on every page, and the
    # desk renders the header now that index() goes through PageProcessor (a
    # decorated context has `platform`, which is what the header is gated on).
    # Before that the tests passed WITHOUT this line — by accident: the missing
    # decoration suppressed the header, which is the very bug being fixed.
    # Same line, same reason as toto.kanban.testing.urls.
    path("", include("toto.sso_master.urls", namespace="sso")),
]
