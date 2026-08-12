from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class PrimulaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Sheets usage event"
        verbose_name_plural = "Sheets usage events"


class PrimulaQuotaPolicy(AbstractQuotaPolicy):
    events = PrimulaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Sheets quota policy"
        verbose_name_plural = "Sheets quota policies"
