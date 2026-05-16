from django.db import models
from toto.core.domain import DomainEntity


class Travel(DomainEntity):
    participants = models.ManyToManyField(
        "people.Person",
        related_name="travels",
        help_text="People participating in this travel",
    )

    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="travels",
        help_text="Route used for this travel",
    )

    info = models.TextField(
        blank=True,
        help_text="Additional information about the travel",
    )

    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Travel score from 1 to 5",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this travel was reviewed or scored",
    )

    starts_at = models.DateTimeField(
        help_text="Travel start date and time",
    )

    ends_at = models.DateTimeField(
        help_text="Travel end date and time",
    )

    @property
    def duration(self):
        return self.ends_at - self.starts_at

    @property
    def duration_display(self):
        delta = self.duration
        total = int(delta.total_seconds())
        if total <= 0:
            return None
        days = delta.days
        hours, rem = divmod(total % 86400, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{days}d {hours}h" if hours else f"{days}d"
        if hours > 0:
            return f"{hours}h {minutes}m" if minutes else f"{hours}h"
        return f"{minutes}m"

    def __str__(self):
        route_name = self.route.name if self.route else "No route"
        return f"Travel via {route_name} from {self.starts_at} to {self.ends_at}"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(score__gte=1, score__lte=5)
                    | models.Q(score__isnull=True)
                ),
                name="travel_score_between_1_and_5",
            )
        ]


class Visit(DomainEntity):
    participant = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="visits",
        help_text="Person who made the visit",
    )

    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits",
        help_text="Location that was visited",
    )

    visited_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the visit happened",
    )

    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the visit ended (optional)",
    )

    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Score from 1 to 5",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this visit review was submitted",
    )

    review = models.TextField(
        blank=True,
        null=True,
        help_text="Optional review text",
    )

    @property
    def length_of_stay(self):
        if self.visited_at and self.ends_at:
            return self.ends_at - self.visited_at
        return None

    @property
    def length_of_stay_display(self):
        delta = self.length_of_stay
        if not delta:
            return None
        total = int(delta.total_seconds())
        if total <= 0:
            return None
        days = delta.days
        hours, rem = divmod(total % 86400, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{days}d {hours}h" if hours else f"{days}d"
        if hours > 0:
            return f"{hours}h {minutes}m" if minutes else f"{hours}h"
        return f"{minutes}m"

    def __str__(self):
        return f"Visit by {self.participant} to {self.location or 'Unknown location'}"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(score__gte=1, score__lte=5)
                    | models.Q(score__isnull=True)
                ),
                name="visit_score_between_1_and_5",
            )
        ]
