"""What socialhub meters: the head tax.

One metric, and it is unlike every other one on the platform. The rest measure
something you did or something you hold; this measures *being a member*, which
is why it has no limit — there is no request to refuse and no amount to cap.

The rate is one number, platform-wide. What varies is the QUANTITY: a member of
a trusted community counts as fewer heads, and a member of one the platform
trusts less counts as more. That is the whole mechanism, and it is why no price
here is ever per-person.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="civics.head",
    label=_("Head tax"),
    app_label="socialhub",
    unit="head_day",
    description=_(
        "A daily charge for being a member of the platform, weighted by how "
        "much the platform trusts your communities. Everyone pays it; the "
        "federation pays its offices out of it."
    ),
    # No default_limit, deliberately: a limit answers "may you do this again",
    # and there is nothing here to do again.
))
