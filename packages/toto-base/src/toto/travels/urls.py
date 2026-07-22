from django.urls import path
from . import views

app_name = "travels"

urlpatterns = [
    path("", views.my_travels, name="my_travels"),
    path("visits/", views.my_visits, name="my_visits"),
    path("metrics/", views.travel_metrics, name="travel_metrics"),
    path("travels/<int:pk>/review/", views.travel_review, name="travel_review"),
    path("travels/new/", views.travel_create, name="travel_create"),
    path("travels/<int:pk>/info/", views.update_travel_info, name="update_travel_info"),
    path("visits/new/", views.visit_create, name="visit_create"),
    path("visits/<int:address_id>/review/", views.visit_review, name="visit_review"),
    path("visits/<int:address_id>/review/submit/", views.submit_visit_review, name="submit_visit_review"),
    path("travels/<int:pk>/delete/", views.travel_delete, name="travel_delete"),
    path("visits/<int:pk>/delete/", views.visit_delete, name="visit_delete"),
]
