"""The community fee — the one tax income that lands in its own account.

Deliberately only one source, though tax runs two things. The per-metric
capacity levies (storage.gb_day, time.hold) bill through toto.quota.charge and
therefore through the tariffs pipeline, crediting TariffItem.receiving_account —
which defaults to the usage-fee account. Their income is already counted under
"Metered usage" and declaring it again here would double it.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.fees import FeeSource, registry

from .surplus import COMMUNITY_FEE_ACCOUNT_CODE


class CommunityFee(FeeSource):
    code = "tax.community"
    account_code = COMMUNITY_FEE_ACCOUNT_CODE
    label = _("Community fee")
    description = _(
        "A periodic percentage of holdings above a threshold, deducted in the "
        "same asset it is held in.")
    settings_url = "quota:index"
    icon = "fa-solid fa-scale-balanced"


registry.register(CommunityFee())
