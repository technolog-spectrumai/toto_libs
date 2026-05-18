from django.urls import path
from . import views

app_name = "sso"  # mirrors sso_master so LOGIN_URL = "sso:login" works in both portal and regis

urlpatterns = [
    path("login/", views.oidc_login, name="login"),
    path("logout/", views.oidc_logout, name="logout"),
    path("callback/", views.oidc_callback, name="callback"),
]
