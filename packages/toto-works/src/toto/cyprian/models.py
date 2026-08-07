"""
``toto.cyprian`` stores nothing in the database.

A document is one self-contained XML vault file (``file_type="document"``)
parsed by :mod:`toto.cyprian.document_format` — the same arrangement memo uses
for a slide deck. That is what makes a document movable, downloadable,
backup-able and shareable with the vault tools that already exist, and what
means it can never half-exist.

This module exists so Django's app machinery has something to import, and so
this paragraph has somewhere to live.
"""

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metric: cyprian.pdf (one WeasyPrint render, synchronous in the request).

class CyprianUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Documents usage event"
        verbose_name_plural = "Documents usage events"


class CyprianQuotaPolicy(AbstractQuotaPolicy):
    events = CyprianUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Documents quota policy"
        verbose_name_plural = "Documents quota policies"
