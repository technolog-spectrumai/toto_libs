"""My account, mounted by the host at ``/account/`` (2026-09-30).

Its own module and namespace rather than a route under ``/socialhub/``: the
page is the member's, reached from the top bar on every page, and its address
should not move with the socialhub's prefix. See `views/account.py`.
"""

from django.urls import path

from toto.socialhub.views.account import account, account_profile, account_timezone

app_name = "account"

urlpatterns = [
    path("", account, name="home"),
    path("profile/", account_profile, name="profile"),
    path("timezone/", account_timezone, name="timezone"),
]
