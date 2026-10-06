from django.urls import path

from . import locations, views

app_name = "geography"

urlpatterns = [
    # The Locations app (stage 64): one page for every signed-in member.
    path("", locations.page, name="locations"),

    path("api/search/", views.search, name="search"),
    path("api/route/", views.route, name="route"),
    path("me/address/", views.my_address, name="my_address"),
    path("me/address/clear/", views.my_address_clear, name="my_address_clear"),
    path("communities/<slug:slug>/headquarters/", views.headquarters, name="headquarters"),
    path("communities/<slug:slug>/headquarters/clear/", views.headquarters_clear,
         name="headquarters_clear"),
    path("communities/<slug:slug>/zone/", views.zone, name="zone"),
    path("communities/<slug:slug>/zone/clear/", views.zone_clear, name="zone_clear"),

    # Members' contributions to a community (stage 64). Every lookup filters
    # on that community and answers 404 otherwise.
    path("communities/<slug:slug>/pins/", locations.pin_create, name="pin_create"),
    path("communities/<slug:slug>/zones/", locations.zone_create, name="zone_create"),
    path("communities/<slug:slug>/pins/<uuid:uid>/", locations.pin_detail, name="pin_detail"),
    path("communities/<slug:slug>/pins/<uuid:uid>/delete/", locations.pin_delete,
         name="pin_delete"),
    path("communities/<slug:slug>/pins/<uuid:uid>/hide/", locations.pin_hide,
         name="pin_hide"),
    path("communities/<slug:slug>/pins/<uuid:uid>/restore/", locations.pin_restore,
         name="pin_restore"),
    path("communities/<slug:slug>/zones/<uuid:uid>/", locations.zone_detail,
         name="zone_detail"),
    path("communities/<slug:slug>/zones/<uuid:uid>/delete/", locations.zone_delete,
         name="zone_delete"),
    path("communities/<slug:slug>/zones/<uuid:uid>/hide/", locations.zone_hide,
         name="zone_hide"),
    path("communities/<slug:slug>/zones/<uuid:uid>/restore/", locations.zone_restore,
         name="zone_restore"),

    # Comments: the community is resolved from the pin or zone. The
    # two-argument shape {% comment_thread %} reverses.
    path("pins/<uuid:uid>/comments/", locations.pin_comment_add, name="pin_comment_add"),
    path("pins/<uuid:uid>/comments/<int:pk>/edit/", locations.pin_comment_edit,
         name="pin_comment_edit"),
    path("pins/<uuid:uid>/comments/<int:pk>/withdraw/", locations.pin_comment_withdraw,
         name="pin_comment_withdraw"),
    path("zones/<uuid:uid>/comments/", locations.zone_comment_add, name="zone_comment_add"),
    path("zones/<uuid:uid>/comments/<int:pk>/edit/", locations.zone_comment_edit,
         name="zone_comment_edit"),
    path("zones/<uuid:uid>/comments/<int:pk>/withdraw/", locations.zone_comment_withdraw,
         name="zone_comment_withdraw"),

    # One's own rows, member of the community or not.
    path("me/contributions/", locations.my_contributions, name="my_contributions"),
    path("me/contributions/pins/<uuid:uid>/delete/", locations.my_pin_delete,
         name="my_pin_delete"),
    path("me/contributions/zones/<uuid:uid>/delete/", locations.my_zone_delete,
         name="my_zone_delete"),
    path("me/contributions/comments/<int:pk>/withdraw/", locations.my_comment_withdraw,
         name="my_comment_withdraw"),
]
