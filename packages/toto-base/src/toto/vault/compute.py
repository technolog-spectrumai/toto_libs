"""What a run of the vault's costs, by how much it works (2026-10-12).

The owner: "make more clever workflow runs costs - it must depend on how much
resource it is using and must be approximated (dont go too deep). So for
example encrypting large file takes more than a small file. Same for zipping."

Mana is the fairness of compute: one person must not take the worker from
everybody else. Disk is the quota's (``storage.*``). So the vault's own runs
(encrypt, decrypt, zip) are charged on ``workflows.compute_mb``, the
megabytes the run will process, estimated where it is started from the sizes
the vault already knows. ``check`` before the work is queued, ``charge`` once
it is; both through ``toto.quota.charge``, so a host with no charger pays
nothing and one with Discipline pays mana.
"""

from __future__ import annotations

from decimal import Decimal

from toto.quota.charge import InsufficientFunds  # noqa: F401 - what callers catch

MB = Decimal("1048576")
METRIC = "workflows.compute_mb"
APP = "workflows"


def megabytes(n_bytes) -> Decimal:
    """Bytes as megabytes, never less than a hundredth: a tiny file is work too."""
    mb = Decimal(str(n_bytes or 0)) / MB
    return max(mb, Decimal("0.01"))


def of_files(files) -> Decimal:
    """The megabytes of these vault files together."""
    return megabytes(sum(int(getattr(f, "file_size_bytes", 0) or 0) for f in files))


def check(user, mb: Decimal) -> None:
    """Raise ``InsufficientFunds`` unless ``user`` can pay for ``mb`` of work."""
    from toto.quota.charge import check_funds, price_for

    check_funds(user, price_for(user, APP), METRIC, mb, "MB")


def charge(user, mb: Decimal, *, source_type: str, source_id) -> None:
    """Take the cost of ``mb`` of work, now that it is queued (never raises:
    the work is on its way; a balance that fell short meanwhile gives what it
    has)."""
    from toto.quota.charge import charge as take, price_for

    take(user, price_for(user, APP), METRIC, mb, unit="MB",
         source_type=source_type, source_id=str(source_id))
