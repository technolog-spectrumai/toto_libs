from django.urls import path
from . import views

app_name = "polls"

urlpatterns = [
    path("", views.poll_list, name="poll_list"),
    path("<slug:slug>/", views.poll_detail, name="poll_detail"),
    path("<slug:slug>/results/", views.poll_results, name="poll_results")
]
