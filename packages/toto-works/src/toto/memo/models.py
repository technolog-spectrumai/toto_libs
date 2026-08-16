"""
``toto.memo`` no longer stores anything in the database.

Presentations now live entirely as self-contained ``.pml`` vault files
(``file_type="presentation"``) parsed by :mod:`toto.memo.presentation_format`.
The memo app is purely the viewer + browser editor for those files — see
:mod:`toto.memo.views`.  The former DB models (Tag / MemoDiagram / MemoDeck /
MemoCard) were dropped in migration ``0002_drop_memo_models``.
"""

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metric: memo.pdf (one WeasyPrint render, synchronous in the request).

class MemoUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Presentations usage event"
        verbose_name_plural = "Presentations usage events"


class MemoQuotaPolicy(AbstractQuotaPolicy):
    events = MemoUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Presentations quota policy"
        verbose_name_plural = "Presentations quota policies"
