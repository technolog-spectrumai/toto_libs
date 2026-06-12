from django.urls import path

from . import views

app_name = "nomad"

urlpatterns = [
    path("migrate", views.MigrateOnionView.as_view(), name="migrate"),
]
