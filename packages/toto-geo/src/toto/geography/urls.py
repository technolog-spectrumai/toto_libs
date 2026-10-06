from django.urls import path

from . import views

app_name = "geography"

urlpatterns = [
    path("api/search/", views.search, name="search"),
    path("api/route/", views.route, name="route"),
    path("me/address/", views.my_address, name="my_address"),
    path("me/address/clear/", views.my_address_clear, name="my_address_clear"),
    path("communities/<slug:slug>/headquarters/", views.headquarters, name="headquarters"),
    path("communities/<slug:slug>/headquarters/clear/", views.headquarters_clear,
         name="headquarters_clear"),
    path("communities/<slug:slug>/zone/", views.zone, name="zone"),
    path("communities/<slug:slug>/zone/clear/", views.zone_clear, name="zone_clear"),
]
