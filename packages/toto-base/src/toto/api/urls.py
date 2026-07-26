"""Framework-level auth/identity endpoints, mounted at ``/api/``.

The host also mounts this urlconf under the legacy ``/telegraph/api/`` prefix, because a
shipped enigma desktop binary calls ``/telegraph/api/{login,logout,me}/`` and cannot be
updated in lockstep with the server. Those three endpoints are the only external contract
here; nothing chat-related lives at that prefix any more.
"""
from django.urls import path

from .auth_views import (
    AppsApiView,
    HealthApiView,
    LoginApiView,
    LogoutApiView,
    MeApiView,
    MeshMeApiView,
)

app_name = "api"

urlpatterns = [
    path("health/", HealthApiView.as_view(), name="health"),
    path("apps/", AppsApiView.as_view(), name="apps"),
    path("login/", LoginApiView.as_view(), name="login"),
    path("logout/", LogoutApiView.as_view(), name="logout"),
    path("me/", MeApiView.as_view(), name="me"),
    path("me/mesh/", MeshMeApiView.as_view(), name="me_mesh"),
]
