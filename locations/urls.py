from django.urls import path
from . import views

app_name = "locations"

urlpatterns = [
    path("", views.locations_all, name="locations_all"),
]

