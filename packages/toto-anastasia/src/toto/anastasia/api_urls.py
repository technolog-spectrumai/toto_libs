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
    path("pool", api.pool, name="pool"),
    path("capsules", api.capsule_list, name="capsule_list"),
    path("capsules/new", api.capsule_create, name="capsule_create"),
    path("capsules/<uuid:uuid>", api.capsule_detail, name="capsule_detail"),
    path("capsules/<uuid:uuid>/<str:action>", api.capsule_action,
         name="capsule_action"),
    path("capsules/<uuid:uuid>/jobs", api.job_create, name="job_create"),
    path("jobs/<uuid:uuid>", api.job_detail, name="job_detail"),
]
