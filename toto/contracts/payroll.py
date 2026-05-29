"""
Compatibility shim — payroll has moved to toto.payroll.services.

Import from toto.payroll.services instead. This module will be removed
in a future release.
"""
import warnings

warnings.warn(
    "toto.contracts.payroll is deprecated; import from toto.payroll.services instead.",
    DeprecationWarning,
    stacklevel=2,
)

from toto.payroll.services import (  # noqa: F401, E402
    approve_payroll_duty,
    create_payroll_contract,
    create_payroll_duty,
    mark_payroll_due,
    settle_payroll_duty,
)
