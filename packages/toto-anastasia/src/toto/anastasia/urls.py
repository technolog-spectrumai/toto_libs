"""Routes for the Compute Gears desk.

The uuid converter, not a plain string: a Gear is addressed by an opaque id and
a malformed one should 404 at the router rather than reach a query.
"""

from django.urls import path

from . import views

app_name = "anastasia"

urlpatterns = [
    path("", views.index, name="index"),
    path("reserve/", views.reserve, name="reserve"),
    path("pool/", views.pool, name="pool"),
    path("<uuid:uuid>/mount/", views.mount, name="mount"),
    path("<uuid:uuid>/unmount/", views.unmount, name="unmount"),
    path("<uuid:uuid>/release/", views.release, name="release"),
    path("<uuid:uuid>/status/", views.status, name="status"),
    # The operator's page. Under the same namespace rather than a second app:
    # it is the same subsystem seen by somebody with different questions, and
    # a separate app would need its own permissions story for no gain.
    path("operations/", views.operator, name="operator"),
    path("operations/<str:action>/", views.operator_control,
         name="operator_control"),
]
