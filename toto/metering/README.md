# toto.metering

Pure usage measurement library. No billing, no ledger, no views, no URLs.

## What it does

- **Abstract model bases** — `AbstractUsageEvent`, `AbstractUsageQuota`. Each source app subclasses these to own its own DB tables.
- **Generic services** — `record_usage()`, `void_usage_event()`, `supersede_usage_event()`.
- **Quota enforcement** — `evaluate_quota()`, `usage_total_for_quota()`. Modes: TRACK / WARN / BLOCK.
- **In-memory metric registry** — `MetricRegistry` / `metric_registry`. Holds `MetricSpec` declarations in memory.
- **Best-effort helper** — `safe_record_usage()`. Swallows all errors so metering failures never break source operations.
- **Usage statement export** — `build_usage_statement()`, `dump_usage_statement_yaml()`. Generates billing-ready YAML for the tariffs handoff.

## What it is NOT

- Not a billing app. It never writes ledger transactions.
- Not a reporting app. It has no views or URLs.
- Not a tariffs app. It does not know about prices, assets, or accounts.

## Source app pattern

```python
# myapp/models.py
from toto.metering.models import AbstractUsageEvent, AbstractUsageQuota

class MyAppUsageEvent(AbstractUsageEvent):
    class Meta:
        app_label = "myapp"

class MyAppUsageQuota(AbstractUsageQuota):
    class Meta:
        app_label = "myapp"
```

```python
# myapp/views.py or services.py
from toto.metering.utils import safe_record_usage
from .models import MyAppUsageEvent, MyAppUsageQuota

safe_record_usage(
    event_model=MyAppUsageEvent,
    quota_model=MyAppUsageQuota,   # optional
    metric_code="myapp.action",
    quantity=1,
    unit="request",
    source_type="myapp.MyModel",
    source_id=str(obj.pk),
    idempotency_key=f"myapp.action:{obj.pk}",
)
```

## Charging gateway — `metering.charge`

`toto.metering.charge` is the **only** place core apps should import billing functions from. It wraps `toto.tariffs.charge` behind an `apps.is_installed("toto.tariffs")` guard. When tariffs is absent every function is a safe no-op.

```python
from toto.metering.charge import (
    InsufficientBalanceError,
    get_tariff_for_user,
    check_user_can_act,
    charge_user,
)

tariff = get_tariff_for_user(request.user, "myapp")  # None if tariffs absent
if tariff:
    try:
        check_user_can_act(request.user, tariff, "myapp.action", 1)
    except InsufficientBalanceError as exc:
        return error_response(exc)
    # ... do work ...
    charge_user(request.user, tariff, "myapp.action", 1,
                source_type="myapp.MyModel", source_id=str(obj.pk))
```

**Never import directly from `toto.tariffs.charge`.** Import from `toto.metering.charge` instead.

## Viewing metering events

When `toto.tariffs` is installed (i.e. `BUILD_ECONOMY=1`), all raw metering events are visible at:

```
/tariffs/metering/
```

This view (`tariffs:metering_overview`) queries every `AbstractUsageEvent` subclass and shows per-app counts plus the 50 most recent events globally.
