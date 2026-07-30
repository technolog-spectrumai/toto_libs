"""The peer's url tree — the side that only ever serves reads.

Identical to the receiver's today; separate so a test can prove the peer serves no
staff surface, and so the two can diverge without one being a special case of the other.
"""
from django.urls import include, path

urlpatterns = [
    path("", include("toto.core.urls")),
    path("datalink/", include("toto.datalink.urls", namespace="datalink")),
]
