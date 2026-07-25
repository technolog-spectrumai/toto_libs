"""Social login routes.

Deliberately no ``app_name``: this urlconf is included from inside the
``sso``-namespaced urlconfs (sso_master, sso_client, auth_local_urls), so
the names land in that namespace — ``sso:social_login`` /
``sso:social_callback`` — at ``/sso/social/<provider>/...`` in every mode.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("<slug:provider>/login/", views.social_login, name="social_login"),
    path("<slug:provider>/callback/", views.social_callback, name="social_callback"),
]
