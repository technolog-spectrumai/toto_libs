"""Routes for the capsule API.

Mounted at ``/api/v1/`` under its own namespace rather than beside the desk's
routes, because the two have different lifetimes: the desk can be redrawn or
retired, and this cannot — a client in another repository is deployed on its
own schedule and cannot be changed in step with this one.

When a shape must break, ``api_urls_v2.py`` appears beside this and this file
is left alone.
"""

from django.urls import path

from . import api

app_name = "anastasia_api"

urlpatterns = [
    # FIRST, because it is the first call a client makes: it proves the token
    # and names it in one round trip.
    path("me", api.me, name="me"),
    path("pool", api.pool, name="pool"),
    path("capsules", api.capsule_list, name="capsule_list"),
    path("capsules/new", api.capsule_create, name="capsule_create"),
    path("capsules/<uuid:uuid>", api.capsule_detail, name="capsule_detail"),
    path("capsules/<uuid:uuid>/storage", api.capsule_storage,
         name="capsule_storage"),
    path("capsules/<uuid:uuid>/jobs", api.job_create, name="job_create"),
    # Installs: POST starts one, GET lists them. Above the catch-all for the
    # reason the comment below gives.
    path("capsules/<uuid:uuid>/installs", api.install_collection,
         name="install_collection"),
    # The files area. Six fixed segments, every one above the catch-all — and
    # `files` itself is exactly the kind of word `<str:action>` would swallow.
    # The file NAME travels in the body for all of them, never in the path:
    # a name is a path, with slashes and dots and unicode in it, and a URL is
    # the wrong place to carry one.
    path("capsules/<uuid:uuid>/files", api.capsule_file_list,
         name="capsule_file_list"),
    path("capsules/<uuid:uuid>/files/get", api.capsule_file_get,
         name="capsule_file_get"),
    path("capsules/<uuid:uuid>/files/put", api.capsule_file_put,
         name="capsule_file_put"),
    path("capsules/<uuid:uuid>/files/delete", api.capsule_file_delete,
         name="capsule_file_delete"),
    # The two Vault directions. Bytes never pass through the client: the
    # server has both ends.
    path("capsules/<uuid:uuid>/files/from-vault", api.capsule_file_from_vault,
         name="capsule_file_from_vault"),
    path("capsules/<uuid:uuid>/files/to-vault", api.capsule_file_to_vault,
         name="capsule_file_to_vault"),
    # LAST, because `<str:action>` matches anything — including "storage" and
    # "jobs". Django takes the first pattern that matches, so a catch-all
    # placed above its siblings silently swallows them: /storage came back 405
    # (the action view is POST-only) instead of the reading, which reads like
    # a broken endpoint rather than a shadowed route.
    path("capsules/<uuid:uuid>/<str:action>", api.capsule_action,
         name="capsule_action"),
    path("jobs/<uuid:uuid>", api.job_detail, name="job_detail"),
    # Progress while it runs, as opposed to `output` which refuses until the
    # job is finished. Both are needed and they answer different questions.
    path("jobs/<uuid:uuid>/logs", api.job_logs, name="job_logs"),
    path("jobs/<uuid:uuid>/output", api.job_output, name="job_output"),
    # A watched install: reading it advances it. `?since=` keeps a client's
    # place in the log copy.
    path("installs/<uuid:uuid>", api.install_detail, name="install_detail"),
    path("installs/<uuid:uuid>/cancel", api.install_cancel,
         name="install_cancel"),
]
