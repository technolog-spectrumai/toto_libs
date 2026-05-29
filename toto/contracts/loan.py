"""
Compatibility shim — loans have moved to toto.loans.services.

Import from toto.loans.services instead. This module will be removed
in a future release.
"""
import warnings

warnings.warn(
    "toto.contracts.loan is deprecated; import from toto.loans.services instead.",
    DeprecationWarning,
    stacklevel=2,
)

from toto.loans.services import (  # noqa: F401, E402
    REPAYMENT_FREQUENCIES,
    approve_repayment,
    create_loan_contract,
    create_loan_disbursement,
    create_repayment_duty,
    mark_repayment_due,
    settle_repayment,
)
