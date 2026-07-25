"""Local-mode url aliases for the ``sso`` namespace.

Hosts running TOTO_AUTH_MODE=local mount this via
``toto.auth_config.auth_urlpatterns``: base templates hard-reverse
``sso:login``/``sso:logout``, so plain-password hosts still need the
namespace even with no OIDC surface installed.
"""
from django.urls import path

from toto.core import views as core_views

app_name = "sso"

urlpatterns = [
    path("login/", core_views.login_view, name="login"),
    path("logout/", core_views.logout_view, name="logout"),
]
