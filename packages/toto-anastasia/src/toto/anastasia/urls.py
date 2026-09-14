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
    # The Capsule view: one URL per tab, the platform's tab idiom. The bare
    # uuid is Information, because that is what "open this Capsule" means.
    path("<uuid:uuid>/", views.capsule, name="capsule"),
    path("<uuid:uuid>/history/", views.capsule_history, name="capsule_history"),
    path("<uuid:uuid>/files/", views.capsule_files, name="capsule_files"),
    path("<uuid:uuid>/env/", views.capsule_env, name="capsule_env"),
    # Installing, from the Env tab: the same `install.py` the bearer API
    # drives. The run is addressed under its Capsule, so ownership is the
    # Capsule's and a stranger's run is a 404 like a stranger's Capsule.
    path("<uuid:uuid>/env/install/", views.env_install, name="env_install"),
    path("<uuid:uuid>/env/installs/<uuid:run>/", views.env_install_status,
         name="env_install_status"),
    path("<uuid:uuid>/env/installs/<uuid:run>/cancel/", views.env_install_cancel,
         name="env_install_cancel"),
    # The verbs. Each redirects back to the tab it was pressed on.
    path("<uuid:uuid>/mount/", views.mount, name="mount"),
    path("<uuid:uuid>/unmount/", views.unmount, name="unmount"),
    path("<uuid:uuid>/release/", views.release, name="release"),
    # What the pages poll or fetch. GETs, owner-only.
    path("<uuid:uuid>/status/", views.status, name="status"),
    # The retained series behind the History charts. Counts only — see
    # `views.samples`.
    path("<uuid:uuid>/samples/", views.samples, name="samples"),
    # A reading on demand, through the beat's own `samples.take` and its
    # throttle. POST: it writes a row.
    path("<uuid:uuid>/samples/take/", views.take_reading, name="take_reading"),
    path("<uuid:uuid>/storage/", views.storage, name="storage"),
    # The files area. The Files TAB owns `files/`; the raw JSON listing moved
    # to `files/list/` on 2026-09-14. Every write is a thin door onto the same
    # request-free functions the bearer API uses (`transfer.to_capsule`,
    # `transfer.to_bucket`), so a copy costs the same whichever door it came
    # through.
    path("<uuid:uuid>/files/list/", views.files_list, name="files_list"),
    path("<uuid:uuid>/files/download/", views.file_download,
         name="file_download"),
    path("<uuid:uuid>/files/upload/", views.file_upload, name="file_upload"),
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
