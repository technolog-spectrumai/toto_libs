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
    path("sso/userinfo/", views.userinfo, name="userinfo"),
    path("sso/login/", views.login_view, name="login"),
    path("sso/logout/", views.logout_view, name="logout"),
    path("sso/my-profile/", views.my_profile, name="my_profile"),
    path("sso/admin-test/<uuid:pk>/", views.admin_test_login, name="admin_test_login"),
    path("sso/admin-test-callback/", views.admin_test_callback, name="admin_test_callback"),

    # Password reset flow
    path("sso/password-reset/", views.password_reset_view, name="password_reset"),
    path("sso/password-reset/done/", views.password_reset_done_view, name="password_reset_done"),
    path("sso/password-reset/<uidb64>/<token>/", views.password_reset_confirm_view, name="password_reset_confirm"),
    path("sso/password-reset/complete/", views.password_reset_complete_view, name="password_reset_complete"),
]
