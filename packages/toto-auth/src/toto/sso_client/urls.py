from django.urls import path
from . import views

app_name = "sso"  # mirrors sso_master so LOGIN_URL = "sso:login" works on provider and consumer alike

urlpatterns = [
    # "login" is the hybrid entry point (LOGIN_URL resolves here in every mode);
    # "federated_login" is the OIDC round trip on its own, which the hybrid page's
    # button targets and which "login" delegates to when there is no local form.
    path("login/", views.oidc_login, name="login"),
    path("federated-login/", views.federated_login, name="federated_login"),
    # Attaching a provider identity to an account that already exists here. Only
    # the account's own session can start it — that friction is the whole point,
    # and it is what replaced matching an incoming identity on email.
    path("link/", views.federated_link, name="federated_link"),
    path("logout/", views.oidc_logout, name="logout"),
    path("callback/", views.oidc_callback, name="callback"),
    # The child's federation console (staff): show the parent, redeem a pairing code,
    # test the link. Same name as the provider's so a dashboard card links either host.
    path("federation/", views.federation_console, name="federation_console"),
]

# Password reset, from the same sso_core definitions the provider mounts — so a
# consumer host with local accounts of its own has somewhere to send them. This
# urlconf is included at "sso/", so no extra prefix. Django's own
# PasswordResetForm.get_users() filters on has_usable_password(), which means
# this can only ever reach a local account and never a provisioned federated one.
from toto.sso_core.password_reset import urlpatterns as _password_reset_urls  # noqa: E402

urlpatterns += _password_reset_urls()

# Social login rides in the same "sso" namespace, but only when the host
# installs the app (the include would import its models otherwise).
from django.apps import apps as django_apps  # noqa: E402

if django_apps.is_installed("toto.social_login"):
    from django.urls import include  # noqa: E402

    urlpatterns += [path("social/", include("toto.social_login.urls"))]
