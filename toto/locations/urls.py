from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.locations_all, name="locations_all"),
    path("route-search/", views.route_search, name="route_search"),

    path("routes/<int:pk>/review/", views.route_review, name="route_review"),
    path("travels/<int:pk>/review/", views.travel_review, name="travel_review"),
    path(
        "visit/addresses/<int:address_id>/visit-review/",
        views.submit_visit_review,
        name="submit_visit_review",
    ),
    path("addresses/<int:address_id>/visit-review/", views.visit_review, name="visit_review"),
    path("addresses/<int:address_id>/visit-review/submit/", views.submit_visit_review, name="submit_visit_review"),
    path(
        "travels/<int:pk>/info/",
        views.update_travel_info,
        name="update_travel_info",
    ),
]