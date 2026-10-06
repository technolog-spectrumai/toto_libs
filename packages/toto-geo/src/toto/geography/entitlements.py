"""Geography's entry in the plan catalogue: free on every plan.

The map, a person's point and a community's headquarters open to every
member; what costs is priced per action in mana (``metrics``), not by plan.
``SubscriptionsConfig.ready`` autodiscovers this module; a host without
plans never imports it.
"""

from django.utils.translation import gettext_lazy as _

from toto.subscriptions.catalogue import Entitlement, registry

registry.register(Entitlement(
    "geography", _("Geography"), free=True, order=5,
    icon="fa-solid fa-map-location-dot",
    description=_("Your address on a map, your community's headquarters and "
                  "zone, place and route search.")))
