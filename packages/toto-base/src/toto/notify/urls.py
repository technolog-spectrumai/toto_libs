"""The bell's doors, mounted by the host at ``notify/`` (2026-10-04). Each
answers at once: no door here holds a request (2026-10-06)."""

from django.urls import path

from . import views

app_name = "notify"

urlpatterns = [
    path("api/", views.api_list, name="api_list"),
    path("api/read/", views.api_read, name="api_read"),
    path("api/read-all/", views.api_read_all, name="api_read_all"),
]
