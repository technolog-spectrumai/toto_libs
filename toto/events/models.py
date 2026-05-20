from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models

from toto.core.domain import DomainEntity
from toto.people.models import Person


class EventCategory(DomainEntity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class EventBase(DomainEntity):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    category = models.ForeignKey(
        EventCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_events",
    )

    public = models.BooleanField(
        default=True,
        help_text="Check if this event is publicly visible.",
    )

    class Meta:
        abstract = True

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time.")

    def __str__(self):
        return self.title


class ScheduledEvent(EventBase):
    """
    A planned event people may attend: meeting, gathering, appointment,
    workshop, ceremony, etc.
    """

    owner = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_events",
        help_text="Person who created this event.",
    )

    organizers = models.ManyToManyField(
        Person,
        related_name="organized_events",
        blank=True,
        help_text="People who can manage this event and send invites.",
    )

    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_events",
        help_text="Specific address for this event, if applicable.",
    )

    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of participants, if limited.",
    )

    requires_registration = models.BooleanField(default=False)

    class Meta:
        verbose_name = "scheduled event"
        verbose_name_plural = "scheduled events"
        ordering = ["-start_time"]


class EventInvite(DomainEntity):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    event = models.ForeignKey(
        ScheduledEvent,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="event_invites",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    note = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("event", "person")]
        ordering = ["sent_at"]
        verbose_name = "event invite"
        verbose_name_plural = "event invites"

    def __str__(self):
        return f"{self.person} → {self.event} ({self.status})"


class Availability(DomainEntity):
    """
    Availability information used when scheduling events.
    """

    class AvailabilityType(models.TextChoices):
        AVAILABLE = "available", "Available"
        UNAVAILABLE = "unavailable", "Unavailable"
        BUSY = "busy", "Busy"
        RESERVED = "reserved", "Reserved"
        OUT_OF_OFFICE = "out_of_office", "Out of office"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="availabilities",
    )

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    availability_type = models.CharField(
        max_length=30,
        choices=AvailabilityType.choices,
    )

    reason = models.CharField(max_length=200, blank=True)

    blocks_scheduling = models.BooleanField(
        default=True,
        help_text="Whether this availability blocks scheduling.",
    )

    class Meta:
        verbose_name = "availability"
        verbose_name_plural = "availabilities"
        ordering = ["start_time"]

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time.")

    def __str__(self):
        return f"{self.person} — {self.availability_type} — {self.start_time:%Y-%m-%d %H:%M}"
