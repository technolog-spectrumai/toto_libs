"""The bell's doors and the long-poll door, mounted by the host at
``notify/`` (2026-10-04)."""

from django.urls import path

from . import views

app_name = "notify"

urlpatterns = [
    path("api/", views.api_list, name="api_list"),
    path("api/read/", views.api_read, name="api_read"),
    path("api/read-all/", views.api_read_all, name="api_read_all"),
    # "Anything new since my cursor?" — held until there is, or ~25 s.
    path("api/wait/", views.api_wait, name="api_wait"),
]
