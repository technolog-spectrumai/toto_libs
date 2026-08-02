"""The url tree the clearing suite drives — the machine surface only."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("clearing/", include("toto.clearing.urls", namespace="clearing")),
]
