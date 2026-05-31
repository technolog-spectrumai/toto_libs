from .models import (
    FinancialInstrument,
    InstrumentStatus,
    InstrumentType,
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


