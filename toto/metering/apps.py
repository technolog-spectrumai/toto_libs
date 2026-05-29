"""
toto.metering — pure usage/quota style library.

Not a billing app.  Not a ledger app.  Not a reporting app.

Provides:
  - Abstract model bases  (AbstractUsageEvent, AbstractUsageQuota)
  - Primitives            (MetricSpec, UsageRecordInput, QuotaDecision, QuotaExceeded)
  - Generic services      (record_usage, void_usage_event, supersede_usage_event)
  - Quota helpers         (quota_window, evaluate_quota, usage_total_for_quota)
  - In-memory registry    (MetricRegistry / metric_registry)
  - Best-effort helper    (safe_record_usage)

Each source app owns its own metering tables by subclassing the abstract bases.
toto.metering has no concrete DB tables, no views, and no URLs.
"""
from django.apps import AppConfig


class MeteringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.metering"
    label = "metering"
    verbose_name = "Metering (library)"
