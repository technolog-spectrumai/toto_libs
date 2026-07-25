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

# Social login rides in the same "sso" namespace, but only when the host
# installs the app (the include would import its models otherwise).
from django.apps import apps as django_apps  # noqa: E402

if django_apps.is_installed("toto.social_login"):
    from django.urls import include  # noqa: E402

    urlpatterns += [path("social/", include("toto.social_login.urls"))]
