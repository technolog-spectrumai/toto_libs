"""Companies in the plan catalogue: free on every plan (stage 65).

No plan ever sold a company register and none does now: a company is a
community, and communities are everybody's. ``SubscriptionsConfig.ready``
autodiscovers this module; a host without plans never imports it.
"""

from django.utils.translation import gettext_lazy as _

from toto.subscriptions.catalogue import Entitlement, registry

registry.register(Entitlement(
    "companies", _("Companies"), free=True, order=6,
    icon="fa-solid fa-building",
    description=_("A company's ID number and its register of shareholdings, "
                  "on the company's community page.")))
