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
    # The retained series behind the card's charts. A GET, owner-only, and
    # counts only — see `views.samples`.
    path("<uuid:uuid>/samples/", views.samples, name="samples"),
    # The files area, from the desk: one listing the card fetches and three
    # POSTs. Each is a thin door onto the same request-free functions the
    # bearer API uses (`transfer.to_capsule`, `transfer.to_bucket`), so a
    # copy costs the same whichever door it came through.
    path("<uuid:uuid>/files/", views.files, name="files"),
    path("<uuid:uuid>/files/from-vault/", views.file_from_vault,
         name="file_from_vault"),
    path("<uuid:uuid>/files/to-vault/", views.file_to_vault,
         name="file_to_vault"),
    path("<uuid:uuid>/files/delete/", views.file_delete, name="file_delete"),
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
