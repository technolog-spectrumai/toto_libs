from django.urls import path
from . import views

urlpatterns = [
    path("home/", views.home_view, name="home"),
    path("root/", views.root_view, name="root"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("not-implemented/", views.not_implemented, name="not_implemented")
]
