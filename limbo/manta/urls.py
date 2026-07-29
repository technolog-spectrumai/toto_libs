from django.urls import path

from . import views

app_name = "manta"

urlpatterns = [
    path("", views.command_builder, name="command_builder"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("jobs/<int:pk>/status/", views.job_status, name="job_status"),
]
