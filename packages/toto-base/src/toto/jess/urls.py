from django.urls import path

from . import views

app_name = "jess"

urlpatterns = [
    path("", views.outbox, name="outbox"),
    path("compose/", views.compose, name="compose"),
    path("messages/<int:pk>/", views.message_detail, name="message_detail"),
    # The polled endpoint. Staff-gated to 403 rather than a login redirect — a poller
    # that follows a 302 gets an HTML page and loops forever. See views.py's docstring.
    path("messages/<int:pk>/status/", views.message_status, name="message_status"),
    path("messages/<int:pk>/retry/", views.message_retry, name="message_retry"),
]
