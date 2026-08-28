"""Shared setup. The roll is soft, so a test can build one out of nothing."""

from __future__ import annotations

from decimal import Decimal

from toto.voting.models import (
    AttendanceStatus,
    Meeting,
    Proposition,
    RollSource,
    VotingConfiguration,
)
from toto.voting.services import lifecycle


class VotingFactoryMixin:
    def make_configuration(self, **kwargs):
        fields = {"name": "Ordinary resolution", "slug": "ordinary"}
        fields.update(kwargs)
        return VotingConfiguration.objects.create(**fields)

    def make_meeting(self, configuration=None, **kwargs):
        fields = {
            "title": "Annual general meeting",
            "configuration": configuration or self.make_configuration(),
        }
        fields.update(kwargs)
        return Meeting.objects.create(**fields)

    def make_proposition(self, meeting, order=1, **kwargs):
        fields = {
            "title": f"Resolution {order}",
            "resolution_text": "That the thing be done.",
            "order": order,
        }
        fields.update(kwargs)
        return Proposition.objects.create(meeting=meeting, **fields)

    def make_roll(self, meeting, rows, *, present=True, source=RollSource.SELECTED):
        """`rows` is [(name, weight), …]. Refs are derived from the name."""
        lifecycle.set_roll(
            meeting,
            [(f"person:{name.lower()}", name, Decimal(str(weight)))
             for name, weight in rows],
            source=source,
        )
        if present:
            for entry in meeting.roll.all():
                entry.status = AttendanceStatus.PRESENT
                entry.save(update_fields=["status"], _allow_update=True)
        from toto.voting.services.tally import refresh_weights

        refresh_weights(meeting)
        return list(meeting.roll.order_by("voter_name"))

    def open_everything(self, meeting, proposition, opened_by=None):
        lifecycle.open_meeting(meeting, opened_by=opened_by)
        return lifecycle.open_proposition(proposition, opened_by=opened_by)
