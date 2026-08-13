"""What the assistant meters: how often you ask, and how big the asks are.

Two codes rather than one, because the two vary independently. A cap on requests
stops a loop; a cap on tokens stops one enormous document from costing what a
thousand ordinary questions cost. Pricing only requests would make a 200-word
prompt and a 60-page paste the same price, which is the case that actually hurts.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="ai.request",
    label=_("Assistant request"),
    app_label="steven",
    unit="request",
    default_limit=200,
    description=_("One question asked of the assistant, whatever its size."),
))

registry.register(Metric(
    code="ai.tokens_1k",
    label=_("Assistant tokens"),
    app_label="steven",
    unit="1k tokens",
    default_limit=500,
    description=_(
        "Thousands of tokens, counted from the provider's own reply. This is "
        "the one charge on the platform taken AFTER the work rather than "
        "before, because its size is not knowable until the answer arrives."
    ),
))
