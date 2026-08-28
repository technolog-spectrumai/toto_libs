"""The metering pair, and nothing else.

The vault is the store: a drawing is one ``VaultFile`` holding an ordinary SVG,
and its history is ``toto.vault.versions``, shared with cyprian, memo and
primula. This app has no model of a drawing and never will — the file IS the
drawing, which is what lets an SVG made anywhere else open here unchanged.

What is here is what ``toto.quota`` cannot own: it declares no tables, so each
metered app carries its own concrete pair and the rows live in that app's
migrations. See toto/quota/models.py.
"""

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class SketchUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Drawings usage event"
        verbose_name_plural = "Drawings usage events"


class SketchQuotaPolicy(AbstractQuotaPolicy):
    events = SketchUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Drawings quota policy"
        verbose_name_plural = "Drawings quota policies"
