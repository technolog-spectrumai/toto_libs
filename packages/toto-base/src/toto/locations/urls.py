from django.urls import path

from . import views
from .api_views import (
    ZoneListApiView, AddressListCreateApiView, AddressDetailApiView,
    MapDataApiView, MapLayersApiView, RouteSearchApiView,
)

app_name = "locations"

urlpatterns = [
    # Enigma JSON API
    path("api/zones/", ZoneListApiView.as_view(), name="api_zone_list"),
    path("api/addresses/", AddressListCreateApiView.as_view(), name="api_address_list"),
    path("api/addresses/<int:pk>/", AddressDetailApiView.as_view(), name="api_address_detail"),
    path("api/map/", MapDataApiView.as_view(), name="api_map_data"),
    path("api/layers/", MapLayersApiView.as_view(), name="api_map_layers"),
    path("api/route-search/", RouteSearchApiView.as_view(), name="api_route_search"),


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

    # Generic per-object detail page + JSON/YAML metadata editor
    path("detail/<str:kind>/<int:pk>/", views.location_detail, name="location_detail"),
    path("metadata/<str:kind>/<int:pk>/save/", views.metadata_save, name="metadata_save"),
    path("metadata/convert/", views.metadata_convert, name="metadata_convert"),
    path("note/<str:kind>/<int:pk>/save/", views.note_save, name="note_save"),
    path("locations/search/", views.location_search_api, name="location_search_api"),
    path("layers/import/", views.api_import_layer, name="api_import_layer"),
]


# GIS-off (BUILD_GEO=0): the locations UI/API is entirely geometry-driven and
# cannot function without geometry columns. Rather than unmount the app (which
# would make every cross-app {% url 'locations:...' %} link and the dashboard
# card raise NoReverseMatch), keep all URL names resolvable but make each view
# return 404 — so a geometry-less host degrades cleanly instead of 500-ing on an
# AttributeError/FieldError. A host that wants the UI gone entirely simply omits
# the include (as faros does). No-op on a GIS build.
from django.conf import settings as _settings  # noqa: E402

if not getattr(_settings, "HAS_GIS", True):
    from django.http import Http404

    def _gis_disabled(request, *args, **kwargs):
        raise Http404("The locations map UI requires a GIS build (BUILD_GEO=1).")

    for _pattern in urlpatterns:
        _pattern.callback = _gis_disabled