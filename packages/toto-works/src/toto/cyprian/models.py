"""No content models — a document is one vault file and nothing more.

What lives here is the metering pair. ``toto.quota`` owns no tables, so every
metered app declares its own concrete ``UsageEvent``/``QuotaPolicy`` and the
rows live in that app's migrations. See toto/quota/models.py.

History: cyprian carried this pair for its PDF export, and ``0003_drop_quota``
removed it when the export left with the desktop editors. It is back for a
different metric — ``cyprian.save`` — because the writer is metered now. Same
tables, same reason they are per-app, different thing being counted.
"""

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class CyprianUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Documents usage event"
        verbose_name_plural = "Documents usage events"


class CyprianQuotaPolicy(AbstractQuotaPolicy):
    events = CyprianUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Documents quota policy"
        verbose_name_plural = "Documents quota policies"
