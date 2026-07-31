from django.urls import path

from . import views

app_name = "jess"

urlpatterns = [
    path("", views.outbox, name="outbox"),
    path("account/", views.account, name="account"),
    path("compose/", views.compose, name="compose"),
    # The inbox: mail this account has received. Fetched on demand — see views.inbox_fetch.
    path("inbox/", views.inbox, name="inbox"),
    path("inbox/fetch/", views.inbox_fetch, name="inbox_fetch"),
    path("inbox/<int:pk>/", views.inbound_detail, name="inbound_detail"),
    path("inbox/<int:pk>/reply/", views.inbox_reply, name="inbox_reply"),
    path("messages/<int:pk>/", views.message_detail, name="message_detail"),
    # The polled endpoint. Staff-gated to 403 rather than a login redirect — a poller
    # that follows a 302 gets an HTML page and loops forever. See views.py's docstring.
    path("messages/<int:pk>/status/", views.message_status, name="message_status"),
    path("messages/<int:pk>/retry/", views.message_retry, name="message_retry"),
    # Manual-release custody: type the passphrase to send held mail, and manage the
    # vault. The passphrase never leaves the request that carries it.
    path("release/", views.release, name="release"),
    path("vault/set-up/", views.vault_setup, name="vault_setup"),
    path("vault/password/", views.provider_secret, name="provider_secret"),
    path("vault/passphrase/", views.vault_rotate_passphrase, name="rotate_passphrase"),
]
