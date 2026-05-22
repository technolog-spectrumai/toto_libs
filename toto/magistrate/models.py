from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def _unique_slug(instance, value):
    base = slugify(value) or instance.__class__.__name__.lower()
    slug = base
    n = 1
    qs = instance.__class__.objects.exclude(pk=instance.pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class MagistrateRole(models.Model):
    """
    Office definition — e.g. Prefect, Tribune, Legate, Quaestor.
    Admin-managed; seeded by ingress_magistrate.
    """
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-scroll")

    # Oversight domains
    overseeing_mobilization = models.BooleanField(
        default=False,
        help_text="Holder may act on emergency declarations without full assembly vote.",
    )
    overseeing_tribunal = models.BooleanField(
        default=False,
        help_text="Holder oversees tribunal proceedings.",
    )
    overseeing_trade = models.BooleanField(
        default=False,
        help_text="Holder oversees trade and commerce.",
    )
    overseeing_finance = models.BooleanField(
        default=False,
        help_text="Holder oversees community finances and taxation.",
    )
    overseeing_public_order = models.BooleanField(
        default=False,
        help_text="Holder oversees public order and safety.",
    )
    overseeing_legislation = models.BooleanField(
        default=False,
        help_text="Holder may propose and fast-track legislation.",
    )

    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Magistrate role"
        verbose_name_plural = "Magistrate roles"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(self, self.name)
        super().save(*args, **kwargs)

    @property
    def oversight_domains(self) -> list[str]:
        mapping = [
            ("overseeing_mobilization", "Mobilization"),
            ("overseeing_tribunal", "Tribunal"),
            ("overseeing_trade", "Trade"),
            ("overseeing_finance", "Finance"),
            ("overseeing_public_order", "Public Order"),
            ("overseeing_legislation", "Legislation"),
        ]
        return [label for field, label in mapping if getattr(self, field)]


class Magistrate(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("impeached", "Impeached"),
        ("term_ended", "Term Ended"),
    ]

    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="magistrate_seats",
    )
    role = models.ForeignKey(
        MagistrateRole,
        on_delete=models.CASCADE,
        related_name="holders",
    )
    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="magistrates",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)

    term_start = models.DateField()
    term_end = models.DateField(null=True, blank=True)

    elected_at = models.DateTimeField(default=timezone.now)
    source_proposal = models.ForeignKey(
        "assembly.AssemblyProposal",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="elected_magistrates",
    )

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One active seat per person per role per community; historical seats allowed after term_ended
        ordering = ["-elected_at"]
        verbose_name = "Magistrate"
        verbose_name_plural = "Magistrates"
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["role", "status"]),
        ]

    def __str__(self):
        return f"{self.person} — {self.role} ({self.community})"

    def clean(self):
        if self.person_id:
            from toto.people.models import Person
            p = Person.objects.get(pk=self.person_id)
            if not p.is_federal_agent:
                raise ValidationError("Only federal agents may hold magistrate roles.")

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if self.term_end and timezone.now().date() > self.term_end:
            return False
        return True


class MagistrateReport(models.Model):
    """
    Periodic report submitted by a magistrate to the assembly.
    Acknowledged by an assembly member — creates an auditable record.
    """
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("acknowledged", "Acknowledged"),
    ]

    magistrate = models.ForeignKey(
        Magistrate,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    title = models.CharField(max_length=255)
    body = models.TextField()

    reporting_period_start = models.DateField(null=True, blank=True)
    reporting_period_end = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    acknowledged_by = models.ForeignKey(
        "people.Person",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="acknowledged_magistrate_reports",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Magistrate report"
        verbose_name_plural = "Magistrate reports"

    def __str__(self):
        return self.title
