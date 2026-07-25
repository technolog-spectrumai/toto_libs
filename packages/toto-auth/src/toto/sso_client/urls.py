from django.urls import path
from . import views

app_name = "sso"  # mirrors sso_master so LOGIN_URL = "sso:login" works on provider and consumer alike

urlpatterns = [
    path("login/", views.oidc_login, name="login"),
    path("logout/", views.oidc_logout, name="logout"),
    path("callback/", views.oidc_callback, name="callback"),
]

# Social login rides in the same "sso" namespace, but only when the host
# installs the app (the include would import its models otherwise).
from django.apps import apps as django_apps  # noqa: E402

if django_apps.is_installed("toto.social_login"):
    from django.urls import include  # noqa: E402

    urlpatterns += [path("social/", include("toto.social_login.urls"))]
