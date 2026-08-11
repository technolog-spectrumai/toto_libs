from django.urls import path

from . import views

app_name = "mint"

urlpatterns = [
    path("", views.index, name="index"),
    path("issue/", views.issue, name="issue"),
]
