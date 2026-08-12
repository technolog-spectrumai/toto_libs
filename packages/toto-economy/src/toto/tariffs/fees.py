"""Metered usage — what the rate card charges for actions as they happen."""

from django.utils.translation import gettext_lazy as _

from toto.quota.fees import FeeSource, registry

from .rate_card import REVENUE_ACCOUNT_CODE


class MeteredUsageFee(FeeSource):
    code = "tariffs.usage"
    account_code = REVENUE_ACCOUNT_CODE
    label = _("Metered usage")
    description = _(
        "Charged per action, before the work runs: a price on the rate card "
        "times the quantity consumed.")
    settings_url = "quota:rate_desk"
    icon = "fa-solid fa-gas-pump"


registry.register(MeteredUsageFee())
