"""A URLconf for the desk's own tests.

The app is mounted by whichever host installs it, so its tests cannot assume a
prefix. This gives them one.
"""

from django.urls import include, path

urlpatterns = [
    path("gears/", include("toto.anastasia.urls", namespace="anastasia")),
]
