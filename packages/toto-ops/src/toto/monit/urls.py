from django.urls import path

from . import views

app_name = "monit"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("health/", views.HealthView.as_view(), name="health"),
]
