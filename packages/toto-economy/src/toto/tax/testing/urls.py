"""The url tree the tax suite drives — mounted the way zenobia would mount it.

The tax pages render the full oya chrome (unlike clearing's machine surface),
so the trees the header reverses on every page ride along, the same set the
primula suite mounts.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("toto.core.urls")),
    # oya/header.html reverses sso:login and sso:logout on every page.
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("quota/", include("toto.quota.urls", namespace="quota")),
    path("tariffs/", include("toto.tariffs.urls", namespace="tariffs")),
    path("tax/", include("toto.tax.urls", namespace="tax")),
    path("mint/", include("toto.mint.urls", namespace="mint")),
    # What the manual's sections reverse on this host's feature set — the
    # suite renders /manual/ to prove the storage-fee section's links resolve.
    path("assets/", include("toto.assets.urls", namespace="assets")),
    path("events/", include("toto.events.urls", namespace="events")),
    path("gervazy/", include("toto.gervazy.urls", namespace="gervazy")),
    path("locations/", include("toto.locations.urls", namespace="locations")),
]
