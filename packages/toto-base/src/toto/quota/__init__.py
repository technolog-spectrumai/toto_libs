"""Usage metering, limits, and the gateway to billing.

Models are abstract — see :mod:`toto.quota.models` for how an app opts in by
declaring its own concrete pair.
"""

from .api import (
    ImproperlyConfiguredQuota,
    # Both of the exceptions check_quota can raise. InArrears was missing here
    # while check_quota was exported, so a caller doing the documented thing —
    # `from toto.quota import check_quota` — had no way to name the second one
    # without reaching into toto.quota.api, and the usual result is catching
    # only QuotaExceeded and letting an unpaid-levy refusal escape as a 500.
    InArrears,
    QuotaExceeded,
    check_quota,
    get_policy,
    period_start,
    record_usage,
    remaining,
    usage_summary,
    used,
)

__all__ = [
    "ImproperlyConfiguredQuota",
    "InArrears",
    "QuotaExceeded",
    "check_quota",
    "get_policy",
    "period_start",
    "record_usage",
    "remaining",
    "usage_summary",
    "used",
]
