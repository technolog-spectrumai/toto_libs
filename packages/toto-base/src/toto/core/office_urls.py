"""Office's routes, mounted at /office/.

A urlconf of its own rather than more entries in `core/urls.py`, for one
reason: core is mounted at `/core/`, and `/core/office/` is not an address for
a headline feature. The views still live in `core.views` — this is a mount, not
an app.

`app_name = "office"` is what `SubscriptionGateMiddleware` reads. Office is not
in the plan catalogue, so it is free; that is correct exactly as long as every
route here is a GET, which is the rule stated in `core/office.py` and asserted
by `tests_office.py`. Anything that writes belongs to the app that owns the
file, or to the vault.
"""
from django.urls import path

from . import views

app_name = "office"

urlpatterns = [
    path("", views.office_view, name="index"),
    # `?file=<id>`, not `open/<id>/`, and declared BEFORE the section slug so
    # the pattern cannot read "open" as the name of a tab. The query string is
    # the shape `vault/_file_tree.html` needs: it builds every row href as
    # `prefix + id` with nothing after it, so a path-style URL would come out
    # missing its trailing slash and lean on APPEND_SLASH to redirect each
    # click. toto.manta's file picker settled the same way ("?file=").
    path("open/", views.office_open, name="open"),
    path("<slug:section>/", views.office_view, name="section"),
]
