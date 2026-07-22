from django.urls import reverse

from toto.locations.plugins.url_plugins import LocationUrlPlugin

LocationUrlPlugin.register("address_visit_review", lambda pk: reverse("travels:visit_review", args=[pk]))
LocationUrlPlugin.register("travel_review", lambda pk: reverse("travels:travel_review", args=[pk]))
LocationUrlPlugin.register("visit_review", lambda pk: reverse("travels:visit_review", args=[pk]))
LocationUrlPlugin.register("travel_create", lambda: reverse("travels:travel_create"))
LocationUrlPlugin.register("visit_create", lambda: reverse("travels:visit_create"))
