"""The receiver's url tree — the side an operator drives.

Kept separate from ``peer_urls`` because both trees declare ``app_name = "datalink"``
and both sides reverse ``datalink:`` names; mounting them side by side would give one
of them the wrong namespace. ``bridge.peer_side`` swaps between them.
"""
from django.urls import include, path

urlpatterns = [
    path("", include("toto.core.urls")),
    path("datalink/", include("toto.datalink.urls", namespace="datalink")),
]
