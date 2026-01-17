from django.urls import path
from . import views

app_name = "polls"

urlpatterns = [
    path("", views.poll_list, name="poll_list"),
    path("<int:pk>/", views.poll_detail, name="poll_detail"),
    path("<int:pk>/results/", views.poll_results, name="poll_results"),
]
