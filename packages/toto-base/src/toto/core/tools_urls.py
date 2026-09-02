"""Tools' routes, mounted at /tools/.

A urlconf of its own (the retired office_urls.py made the argument first):
core is mounted at `/core/`, and `/core/tools/` is not an address for a
headline destination.

`app_name = "tools"` is what `SubscriptionGateMiddleware` reads. Tools is not in
the plan catalogue, so the HUB is free — correct exactly as long as every route
here is a GET. Each tool keeps its own entitlement and its own POSTs in its own
app, which is what `Tool.entitlement` records: this page offers doors, it does
not do work.
"""
from django.urls import path

from . import views

app_name = "tools"

urlpatterns = [
    path("", views.tools_view, name="index"),
]
