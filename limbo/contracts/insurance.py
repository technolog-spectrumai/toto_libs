"""
Compatibility shim — insurance has moved to toto.insurance.services.

Import from toto.insurance.services instead. This module will be removed
in a future release.
"""
import warnings

warnings.warn(
    "toto.contracts.insurance is deprecated; import from toto.insurance.services instead.",
    DeprecationWarning,
    stacklevel=2,
)

from toto.insurance.services import (  # noqa: F401, E402
    PREMIUM_FREQUENCIES,
    approve_claim,
    approve_premium,
    create_claim_duty,
    create_insurance_contract,
    create_premium_duty,
    mark_premium_due,
    settle_claim,
    settle_premium,
)
