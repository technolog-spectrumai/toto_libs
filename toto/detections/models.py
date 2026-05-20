from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
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


# ---------------------------------------------------------------------------
# Claimable work (abstract base shared by Bounty and future service models)
# ---------------------------------------------------------------------------

class ClaimableWork(DomainEntity):
    """
    Abstract base for work that can be claimed, submitted, and rewarded.
    Bounty extends this; bazaar services can too.
    """
    reward_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    reward_currency = models.ForeignKey(
        'assets.Currency', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_rewards',
    )
    reward_asset = models.ForeignKey(
        'assets.Asset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_rewards',
    )
    reward_ledger_account = models.ForeignKey(
        'assets.LedgerAccount', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_rewards',
        help_text='Escrow account holding the reward.',
    )
    max_claims = models.PositiveIntegerField(default=1)
    deadline = models.DateTimeField(null=True, blank=True)
    is_public = models.BooleanField(default=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Bounty
# ---------------------------------------------------------------------------

class BountyBoard(DomainEntity):
    """Container for bounties — owned by a shop, scoped to a territory."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    shop = models.ForeignKey('bazaar.Shop', on_delete=models.CASCADE, related_name='bounty_boards')
    territory = models.ForeignKey(
        'locations.Territory', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty_boards',
    )
    description = models.TextField(blank=True)
    custodian = models.ForeignKey(
        'bazaar.MarketCustodian', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty_boards',
        help_text='Custodian who must approve each submission before reward is released.',
    )
    ledger_account = models.ForeignKey(
        'assets.LedgerAccount', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty_boards',
    )
    currency = models.CharField(max_length=3, default='PLN')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(self, self.name)
        super().save(*args, **kwargs)


class BountyQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status='open', is_public=True, board__is_active=True)


class Bounty(ClaimableWork):
    """
    A regulated work task on a bounty board.
    Optionally linked to a DetectionHandle (detection → handle → bounty pipeline).
    """
    BOUNTY_TYPE_CHOICES = [
        ('task', 'Task'), ('bug_fix', 'Bug Fix'), ('research', 'Research'),
        ('creative', 'Creative'), ('development', 'Development'),
        ('testing', 'Testing'), ('content', 'Content'), ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('open', 'Open'), ('claimed', 'Claimed'),
        ('submitted', 'Submitted'), ('reviewing', 'Reviewing'),
        ('completed', 'Completed'), ('cancelled', 'Cancelled'), ('expired', 'Expired'),
    ]

    board = models.ForeignKey(BountyBoard, on_delete=models.CASCADE, related_name='bounties')
    category = models.ForeignKey(
        DetectionCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounties',
    )
    handle = models.ForeignKey(
        DetectionHandle, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounties',
        help_text='The detection handle this bounty resolves, if any.',
    )
    product = models.OneToOneField(
        'bazaar.Product', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty',
        help_text='The regulated-service product this bounty represents in the bazaar marketplace.',
    )
    created_by = models.ForeignKey(
        'people.Person', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_bounties',
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, blank=True)
    summary = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    bounty_type = models.CharField(max_length=32, choices=BOUNTY_TYPE_CHOICES, default='task')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='draft')
    location = models.ForeignKey(
        'locations.Address', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounties',
    )
    territory = models.ForeignKey(
        'locations.Territory', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounties',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = BountyQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        unique_together = [('board', 'slug')]
        indexes = [
            models.Index(fields=['board', 'status', 'is_public']),
            models.Index(fields=['bounty_type']),
            models.Index(fields=['deadline']),
        ]
        verbose_name_plural = 'bounties'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(self, self.title, scope={'board': self.board})
        if self.status == 'open' and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return bool(self.deadline and timezone.now() > self.deadline and self.status == 'open')

    @property
    def active_claims_count(self):
        return self.claims.filter(status__in=['accepted', 'working', 'submitted']).count()

    @property
    def slots_available(self):
        return self.max_claims - self.active_claims_count


class BountyClaim(DomainEntity):
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('accepted', 'Accepted'), ('working', 'Working'),
        ('submitted', 'Submitted'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('cancelled', 'Cancelled'), ('paid', 'Paid'),
    ]

    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name='claims')
    hunter = models.ForeignKey('people.Person', on_delete=models.CASCADE, related_name='bounty_claims')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty_claims',
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='pending')
    proposal = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('bounty', 'hunter')]

    def __str__(self):
        return f'{self.hunter} → {self.bounty}'


class BountySubmission(DomainEntity):
    STATUS_CHOICES = [
        ('submitted', 'Submitted'), ('revision_requested', 'Revision Requested'),
        ('approved', 'Approved'), ('rejected', 'Rejected'),
    ]

    claim = models.ForeignKey(BountyClaim, on_delete=models.CASCADE, related_name='submissions')
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    attachments = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='submitted')
    reviewer_note = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f'Submission for {self.claim}'


class BountyReview(DomainEntity):
    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name='reviews')
    claim = models.ForeignKey(BountyClaim, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    reviewer = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='given_bounty_reviews')
    hunter = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='received_bounty_reviews')
    rating = models.PositiveSmallIntegerField()
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=models.Q(rating__gte=1, rating__lte=5),
                name='detection_bounty_review_rating_1_to_5',
            )
        ]

    def __str__(self):
        return f'Review {self.rating}/5 for {self.bounty}'


class BountyPayment(DomainEntity):
    claim = models.OneToOneField(BountyClaim, on_delete=models.CASCADE, related_name='payment')
    paid_by = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bounty_payments_issued')
    ledger_account = models.ForeignKey('assets.LedgerAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='bounty_payments')
    receiver_ledger_account = models.ForeignKey(
        'assets.LedgerAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bounty_payments_received',
    )
    asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='bounty_payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.ForeignKey(
        'assets.Currency', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bounty_payments',
    )
    ledger_tx_reference = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    is_settled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f'Payment {self.amount} for {self.claim}'
