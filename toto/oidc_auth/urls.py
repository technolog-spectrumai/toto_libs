from django.urls import path
from . import views

app_name = "oidc_auth"

urlpatterns = [
    path("login/", views.oidc_login, name="login"),
    path("callback/", views.oidc_callback, name="callback"),
]
