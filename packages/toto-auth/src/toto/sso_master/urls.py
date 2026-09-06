from django.urls import path
from . import views
from .api_views import RegisterApiView

app_name = "sso"  # keep namespace "sso" for portal backwards compat

urlpatterns = [
    # JSON auth API for programmatic clients (Enigma Cloud).
    path("sso/api/register/", RegisterApiView.as_view(), name="api_register"),

    path(".well-known/openid-configuration", views.openid_configuration, name="openid_configuration"),
    # Compose-internal variant (public authorize endpoint, internal token/userinfo)
    # — fetched by in-network relying parties like the gitea container.
    path(
        ".well-known/openid-configuration-internal",
        views.openid_configuration_internal,
        name="openid_configuration_internal",
    ),
    path("sso/jwks.json", views.jwks, name="jwks"),
    path("sso/authorize/", views.authorize, name="authorize"),
    path("sso/consent/", views.consent, name="consent"),
    path("sso/token/", views.token, name="token"),
    # The act of federation. Under sso/ so it inherits the nginx rate limit.
    path("sso/enroll/", views.enroll, name="enroll"),
    # This platform's public identity (name + logo) for a federated patron, and the
    # staff console that lists federated platforms and mints their QR codes.
    path("sso/platform-info/", views.platform_info, name="platform_info"),
    path("sso/federation/", views.federation_console, name="federation_console"),
    path("sso/federation/branding/", views.federation_branding,
         name="federation_branding"),
    path("sso/userinfo/", views.userinfo, name="userinfo"),
    path("sso/login/", views.login_view, name="login"),
    path("sso/logout/", views.logout_view, name="logout"),
    path("sso/my-profile/", views.my_profile, name="my_profile"),
    path("sso/admin-test/<uuid:pk>/", views.admin_test_login, name="admin_test_login"),
    path("sso/admin-test-callback/", views.admin_test_callback, name="admin_test_callback"),
]

# Password reset flow — defined in sso_core so the consumer urlconf mounts the
# same four names from the same code. This urlconf is included at "", so it
# passes the "sso/" segment; sso_client's is included at "sso/" and passes none.
from toto.sso_core.password_reset import urlpatterns as _password_reset_urls  # noqa: E402

urlpatterns += _password_reset_urls("sso/")

# Social login rides in the same "sso" namespace, but only when the host
# installs the app (the include would import its models otherwise).
from django.apps import apps as django_apps  # noqa: E402

if django_apps.is_installed("toto.social_login"):
    from django.urls import include  # noqa: E402

    urlpatterns += [path("sso/social/", include("toto.social_login.urls"))]
