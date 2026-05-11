from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.locations_all, name="locations_all"),
    path("route-search/", views.route_search, name="route_search"),

    # Proper detail pages
    path("addresses/<int:pk>/", views.address_detail, name="address_detail"),
    path("zones/<int:pk>/", views.zone_detail, name="zone_detail"),
    path("routes/<int:pk>/", views.route_detail, name="route_detail"),

    # Compatibility / review URLs
    path("routes/<int:pk>/review/", views.route_review, name="route_review"),
    path("addresses/<int:address_id>/visit-review/", views.visit_review, name="visit_review"),

    # Submit visit review
    path(
        "visit/addresses/<int:address_id>/visit-review/",
        views.submit_visit_review,
        name="submit_visit_review_legacy",
    ),
    path(
        "addresses/<int:address_id>/visit-review/submit/",
        views.submit_visit_review,
        name="submit_visit_review",
    ),

    # Travel
    path("travels/<int:pk>/review/", views.travel_review, name="travel_review"),
    path("travels/<int:pk>/summary/", views.travel_summary, name="travel_summary"),
    path("travels/<int:pk>/info/", views.update_travel_info, name="update_travel_info"),
    path("travels/new/", views.travel_create, name="travel_create"),
]