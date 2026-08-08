from django.urls import path

from . import views

app_name = "tax"

urlpatterns = [
    path("", views.my_levies, name="my_levies"),
    path("rules/", views.rules, name="rules"),
    path("demurrage/", views.demurrage, name="demurrage"),
    path("demurrage/set/", views.time_grant_set, name="time_set"),
]
