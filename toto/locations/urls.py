from django.urls import path

from . import views
from .api_views import ZoneListApiView, AddressListCreateApiView, AddressDetailApiView

app_name = "locations"

urlpatterns = [
    # Enigma JSON API
    path("api/zones/", ZoneListApiView.as_view(), name="api_zone_list"),
    path("api/addresses/", AddressListCreateApiView.as_view(), name="api_address_list"),
    path("api/addresses/<int:pk>/", AddressDetailApiView.as_view(), name="api_address_detail"),


    path("", views.locations_all, name="locations_all"),
    path("route-search/", views.route_search, name="route_search"),

    # Proper detail pages
    path("addresses/<int:pk>/", views.address_detail, name="address_detail"),
    path("zones/<int:pk>/", views.zone_detail, name="zone_detail"),
    path("routes/<int:pk>/", views.route_detail, name="route_detail"),

    # Compatibility review URL
    path("routes/<int:pk>/review/", views.route_review, name="route_review"),

    path("addresses/new/", views.address_create, name="address_create"),
    path("routes/save/", views.route_save, name="route_save"),
    path("locations/search/", views.location_search_api, name="location_search_api"),
    path("layers/import/", views.api_import_layer, name="api_import_layer"),
]