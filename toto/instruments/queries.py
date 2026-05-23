from django.db import models as _models

from .models import (
    FinancialInstrument,
    InstrumentStatus,
    InstrumentType,
    LeaseCharge,
    LeaseContract,
    LeaseTariff,
)


def dashboard_counts():
    qs = FinancialInstrument.objects.all()
    return {
        "total": qs.count(),
        "active": qs.filter(status=InstrumentStatus.ACTIVE).count(),
        "draft": qs.filter(status=InstrumentStatus.DRAFT).count(),
        "settled": qs.filter(status=InstrumentStatus.SETTLED).count(),
        "escrows": qs.filter(instrument_type=InstrumentType.ESCROW).count(),
        "forwards": qs.filter(instrument_type=InstrumentType.FORWARD).count(),
        "futures": qs.filter(instrument_type=InstrumentType.FUTURE).count(),
        "options": qs.filter(instrument_type=InstrumentType.OPTION).count(),
        "vesting": qs.filter(instrument_type=InstrumentType.VESTING).count(),
        "staking": qs.filter(instrument_type=InstrumentType.STAKING).count(),
        "subscriptions": qs.filter(instrument_type=InstrumentType.SUBSCRIPTION).count(),
    }


def list_instruments(*, instrument_type=None, status=None):
    qs = FinancialInstrument.objects.select_related("issuer", "contract_account").order_by("-created_at")
    if instrument_type:
        qs = qs.filter(instrument_type=instrument_type)
    if status:
        qs = qs.filter(status=status)
    return qs


# ---------------------------------------------------------------------------
# Lease queries
# ---------------------------------------------------------------------------

def list_active_leases():
    return LeaseContract.objects.filter(status="active").select_related(
        "instrument", "lessee_account", "lessor_account", "payment_asset", "leased_asset"
    )


def list_account_leases(account):
    return LeaseContract.objects.filter(
        _models.Q(lessor_account=account) | _models.Q(lessee_account=account)
    ).select_related("instrument")


def list_lease_charges(lease):
    return LeaseCharge.objects.filter(lease=lease).select_related(
        "tariff", "metric", "transaction"
    )


def list_pending_lease_charges():
    return LeaseCharge.objects.filter(status="pending").select_related(
        "lease__instrument", "lease__payment_asset", "metric"
    )


def list_lease_tariffs(lease):
    return LeaseTariff.objects.filter(lease=lease).select_related("metric")
