from django.urls import path

from . import views

app_name = "sso"

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
]
