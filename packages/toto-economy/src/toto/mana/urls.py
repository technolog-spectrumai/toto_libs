from django.urls import path

from . import views

app_name = "mana"

# Concrete paths above the slug, so "about" and "api" can never be read as a
# colour. Read-only: nothing here writes — the prompt posts to the vault's own
# encrypt door.
urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.about, name="about"),
    path("regeneration/", views.regeneration, name="regeneration"),
    path("api/balances/", views.api_balances, name="api_balances"),
    path("<slug:colour>/", views.colour, name="colour"),
]
