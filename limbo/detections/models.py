from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.events.models import EventBase


def _unique_slug(instance, value, *, scope=None, slug_field='slug'):
    base = slugify(value) or instance.__class__.__name__.lower()
    slug = base
    counter = 1
    qs = instance.__class__.objects.all()
    if scope:
        qs = qs.filter(**scope)
    while qs.filter(**{slug_field: slug}).exclude(pk=instance.pk).exists():
        counter += 1
        slug = f'{base}-{counter}'
    return slug


# ---------------------------------------------------------------------------
# Detection models
# ---------------------------------------------------------------------------

class DetectionCategory(DomainEntity):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='children',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'detection categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(self, self.name)
        super().save(*args, **kwargs)


class Detection(EventBase):
    """
    A real-world detection event — incident, observation, hazard, or anomaly.
    Extends EventBase (start_time = detected at, end_time = resolved/closed at).
    """
    SEVERITY_CHOICES = [
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'),
    ]
    TYPE_CHOICES = [
        ('incident', 'Incident'), ('observation', 'Observation'),
        ('anomaly', 'Anomaly'), ('hazard', 'Hazard'), ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('new', 'New'), ('acknowledged', 'Acknowledged'),
        ('handling', 'In Handling'), ('resolved', 'Resolved'), ('closed', 'Closed'),
    ]

    # Override EventBase.category to use DetectionCategory
    category = models.ForeignKey(
        DetectionCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detections',
    )
    # Override end_time to allow null (detection may not yet be resolved)
    end_time = models.DateTimeField(null=True, blank=True)

    # Location — one of: address, zone, or route
    address = models.ForeignKey(
        'locations.Address', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detections',
    )
    zone = models.ForeignKey(
        'locations.Zone', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detections',
    )
    route = models.ForeignKey(
        'locations.Route', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detections',
    )

    # People
    reported_by = models.ForeignKey(
        'people.Person', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reported_detections',
    )
    involved_persons = models.ManyToManyField(
        'people.Person', blank=True,
        related_name='involved_detections',
    )
    mitigation_task = models.ForeignKey(
        'kanban.Task',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='detection_mitigations',
        help_text='Kanban task that mitigates or resolves this detection.',
    )

    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default='medium')
    detection_type = models.CharField(max_length=24, choices=TYPE_CHOICES, default='incident')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['status', 'severity']),
            models.Index(fields=['detection_type']),
        ]

    def clean(self):
        # Allow null end_time; only validate order when both are set
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            from django.core.exceptions import ValidationError
            raise ValidationError('end_time must be after start_time.')

    @property
    def location_label(self):
        if self.address:
            return str(self.address)
        if self.zone:
            return self.zone.name
        if self.route:
            return self.route.name
        return None

    @property
    def map_geometry(self):
        if self.address and self.address.geometry:
            return self.address.geometry
        if self.zone and self.zone.geometry:
            return self.zone.geometry
        if self.route and self.route.geometry:
            return self.route.geometry
        return None

    def get_absolute_url(self):
        return reverse('detections:detection-detail', kwargs={'pk': self.pk})


class DetectionHandle(DomainEntity):
    """Workflow object that connects a Detection to its resolution effort."""
    STATUS_CHOICES = [
        ('open', 'Open'), ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'), ('resolved', 'Resolved'), ('closed', 'Closed'),
    ]

    detection = models.ForeignKey(Detection, on_delete=models.CASCADE, related_name='handles')
    assigned_to = models.ForeignKey(
        'people.Person', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_handles',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Handle for {self.detection} ({self.get_status_display()})'
