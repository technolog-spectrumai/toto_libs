"""The member's own account, mounted by the host at ``/account/`` (2026-09-30).

Since 2026-10-02 (stage 50) the account is a set of tabs on the member's own
profile, and these are its doors: ``""`` (``account:home``) is the way to that
page — a redirect to the profile on the tab its address meant, or the page
drawn in place for an account with no profile yet — and every other path is a
form's door, which goes back to its tab; ``email/confirm/`` is the link the
e-mail change mails. Its own module and namespace rather than routes under
``/socialhub/``: the header links it on every page, mailed links name it, and
its addresses should not move with the socialhub's prefix. See
`views/account.py`.
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
