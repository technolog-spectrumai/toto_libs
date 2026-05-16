from uuid import uuid4
from django.db import models
from toto.people.models import Person
from toto.core.domain import DomainEntity


class EventCategory(DomainEntity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Event(DomainEntity):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    title = models.CharField(max_length=200)
    description = models.TextField()

    # Structured location links.
    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        help_text="Specific address for this event, if applicable.",
    )

    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        help_text="Route connected to this event, if movement is involved.",
    )

    zone = models.ForeignKey(
        "locations.Zone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        help_text="Zone for this event, if it is geographically scoped.",
    )

    organizer = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organized_events",
    )

    category = models.ForeignKey(
        EventCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    public = models.BooleanField(
        default=True,
        help_text="Check if this event is publicly visible.",
    )

    @property
    def effective_location(self):
        return self.address or self.route or self.zone or self.location

    def __str__(self):
        return self.title