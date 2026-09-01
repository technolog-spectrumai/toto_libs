from django.urls import path

from . import views

app_name = "monit"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("health/", views.HealthView.as_view(), name="health"),
    # "status/", not "database/", and the label above the page says Database.
    # The route, its name and every reverse() of it predate the 2026-09-01
    # merge; renaming the URL would break links people already hold to buy a
    # tidier address, which is a bad trade for a page whose whole subject is
    # whether the record is sound.
    path("status/", views.StatusView.as_view(), name="status"),
    path("history/", views.HistoryView.as_view(), name="history"),
]
