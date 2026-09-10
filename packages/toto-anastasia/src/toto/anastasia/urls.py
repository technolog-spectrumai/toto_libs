"""Routes for the Compute Capsules desk.

The uuid converter, not a plain string: a Capsule is addressed by an opaque id and
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
    # API tokens. Above the operator block and below the capsule verbs,
    # because that is the order a person meets them: reserve a capsule, then
    # mint a token so a desktop client can use it.
    #
    # `tokens/` before `<uuid:uuid>/...` would be a shadowing risk with a
    # `<str:...>` converter; it is not one here because every capsule route
    # uses the `uuid` converter, which refuses the literal "tokens" at the
    # router. Left in this order anyway — the next person to add a route
    # should not have to re-derive that.
    path("tokens/", views.tokens, name="tokens"),
    path("tokens/new/", views.token_issue, name="token_issue"),
    path("tokens/<int:pk>/revoke/", views.token_revoke, name="token_revoke"),
    # The operator's page. Under the same namespace rather than a second app:
    # it is the same subsystem seen by somebody with different questions, and
    # a separate app would need its own permissions story for no gain.
    path("operations/", views.operator, name="operator"),
    path("operations/<str:action>/", views.operator_control,
         name="operator_control"),
]
