"""One metric: a month of subscription.

Deliberately one, and deliberately unlimited. Every other metric on the platform
caps something — a number of requests, a number of megabytes — and refusing the
next one is a sensible answer. There is nothing to refuse here: a month happens
whether or not anybody wants it to, and a cap would mean "you have had too many
months", which is not a thing.

What varies between two subscribers is the QUANTITY: the size of their plan,
less whatever their best community takes off it. The price of one unit is one
number for the whole platform. See ``subscriptions/services.py`` for why it has
to be that way round.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="subscription.month",
    label=_("Subscription"),
    app_label="subscriptions",
    unit="month",
    description=_(
        "One month of a subscription plan. The quantity is the plan's size "
        "after any community discount; the price per unit is the same for "
        "everybody."
    ),
))
