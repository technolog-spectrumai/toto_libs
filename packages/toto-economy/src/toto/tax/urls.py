from django.urls import path

from . import views

app_name = "tax"

urlpatterns = [
    path("", views.my_levies, name="my_levies"),
    path("rules/", views.rules, name="rules"),
    path("demurrage/", views.demurrage, name="demurrage"),
    path("demurrage/set/", views.time_grant_set, name="time_set"),
    # A holding fee belongs to an ASSET, so it is edited on the asset's
    # own page (the plugin in plugins/asset_plugins.py) and this is only
    # the write door. It was the bottom half of tax:rules, whose top half
    # configured levy allowances — a different object entirely.
    path("holding-fee/<int:asset_id>/", views.holding_fee_set, name="holding_fee_set"),
]
