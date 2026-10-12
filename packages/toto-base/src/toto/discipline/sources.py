"""Where mana comes from, beside regeneration.

The platform's features spend mana and never ask where it came from. A
source puts it on an account: an administrator's hand here; a host with an
economy registers one of its own (assets for mana), and another could stand
on an outside payment. Each ends in ``services.grant``.
"""

from __future__ import annotations

from typing import ClassVar

from django.utils.translation import gettext_lazy as _

from toto.core.plugin import BasePlugin


class ManaSource(BasePlugin):
    registry: ClassVar[dict] = {}

    def available(self, user) -> bool:
        """Is this a way for this person to come by mana?"""
        return True

    def describe(self, user) -> str:
        """One sentence for the Mana page and for a refusal."""
        raise NotImplementedError


@ManaSource.plugin(key="admin", title=_("An administrator's grant"), order=10)
class AdminGrantSource(ManaSource):
    def describe(self, user) -> str:
        return _("An administrator can grant mana.")
