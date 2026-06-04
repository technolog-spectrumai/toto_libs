from django.urls import path
from . import views

app_name = "capitol"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("community/set/", views.set_community, name="set_community"),
]
