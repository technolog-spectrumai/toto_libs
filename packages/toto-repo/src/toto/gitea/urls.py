from django.urls import path

from . import views

app_name = "gitea"

urlpatterns = [
    path("", views.index, name="index"),
    path("remotes/", views.remotes, name="remotes"),
]
