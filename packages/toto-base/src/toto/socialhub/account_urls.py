"""My account, mounted by the host at ``/account/`` (2026-09-30).

Its own module and namespace rather than a route under ``/socialhub/``: the
page is the member's, reached from the top bar on every page, and its address
should not move with the socialhub's prefix. See `views/account.py`.
"""

from django.urls import path

from toto.socialhub.views.account import (
    account,
    account_data_export,
    account_email,
    account_email_confirm,
    account_erasure_request,
    account_key_store,
    account_password,
    account_profile,
    account_session_end,
    account_sessions_end_others,
    account_timezone,
)

app_name = "account"

urlpatterns = [
    path("", account, name="home"),
    path("profile/", account_profile, name="profile"),
    path("timezone/", account_timezone, name="timezone"),
    path("password/", account_password, name="password"),
    path("email/", account_email, name="email"),
    path("email/confirm/", account_email_confirm, name="email_confirm"),
    path("key-store/", account_key_store, name="key_store"),
    path("data-export/", account_data_export, name="data_export"),
    path("erasure-request/", account_erasure_request, name="erasure_request"),
    path("sessions/<int:session_id>/end/", account_session_end, name="session_end"),
    path("sessions/end-others/", account_sessions_end_others, name="sessions_end_others"),
]
