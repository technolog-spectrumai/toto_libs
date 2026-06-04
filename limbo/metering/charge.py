"""
toto.metering.charge
====================

Charging gateway — the ONLY place core apps should import billing functions from.

This module wraps toto.tariffs.charge behind an is-installed guard. If
toto.tariffs is not in INSTALLED_APPS every function is a safe no-op and
InsufficientBalanceError is a stub that is never raised.

Usage in any metered app::

    from toto.metering.charge import (
        InsufficientBalanceError,
        get_tariff_for_user,
        check_user_can_act,
        charge_user,
    )

    tariff = get_tariff_for_user(request.user, "vault")
    if tariff:
        try:
            check_user_can_act(request.user, tariff, "storage.request", 1)
        except InsufficientBalanceError as exc:
            return JsonResponse({"error": str(exc)}, status=402)
        # ... do work ...
        charge_user(request.user, tariff, "storage.request", 1,
                    source_type="vault.VaultFile", source_id=str(obj.pk))
"""
from __future__ import annotations


def tariffs_enabled() -> bool:
    from django.apps import apps
    return apps.is_installed("toto.tariffs")


# Re-export InsufficientBalanceError from tariffs when available,
# otherwise provide a stub that is never raised (tariff is None → no checks).
try:
    from toto.tariffs.charge import InsufficientBalanceError
except ImportError:
    class InsufficientBalanceError(Exception):  # type: ignore[no-redef]
        """Stub — only raised when toto.tariffs is installed."""
        asset_name: str = ""
        needed_display: object = 0
        have_display: object = 0
        needed_base_units: int = 0
        have_base_units: int = 0
        asset_decimals: int = 0
        topup_url: str = ""


def get_tariff_for_user(user, app_label: str):
    """Return the best active Tariff for user+app, or None if tariffs absent."""
    if not tariffs_enabled():
        return None
    from toto.tariffs.charge import get_tariff_for_user as _f
    return _f(user, app_label)


def check_user_can_act(user, tariff, metric_code: str, quantity, unit: str = "") -> None:
    """Raise InsufficientBalanceError if user cannot afford the action.

    No-op when tariff is None or tariffs is not installed.
    """
    if not tariff:
        return
    from toto.tariffs.charge import check_user_can_act as _f
    _f(user, tariff, metric_code, quantity, unit)


def charge_user(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Drain prepaid balance for a metered action.

    Returns (UsageRecord, LedgerTransaction) or None when tariff is None.
    """
    if not tariff:
        return None
    from toto.tariffs.charge import charge_user as _f
    return _f(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def check_and_charge(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Atomic check-then-charge wrapper.

    Returns (UsageRecord, LedgerTransaction) or None when tariff is None.
    """
    if not tariff:
        return None
    from toto.tariffs.charge import check_and_charge as _f
    return _f(user, tariff, metric_code, quantity, unit=unit, **kwargs)
