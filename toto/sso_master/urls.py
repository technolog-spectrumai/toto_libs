from django.urls import path
from . import views

app_name = "sso"  # keep namespace "sso" for portal backwards compat

urlpatterns = [
    path(".well-known/openid-configuration", views.openid_configuration, name="openid_configuration"),
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
]
