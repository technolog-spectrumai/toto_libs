from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import random
from django.utils.text import slugify


class Address(models.Model):
    country_name = models.CharField(max_length=2, verbose_name="Country")
    state_or_province_name = models.CharField(max_length=128, verbose_name="State/Province")
    locality_name = models.CharField(max_length=128, verbose_name="Locality")
    street = models.CharField(max_length=255, verbose_name="Street")
    building = models.CharField(max_length=64, verbose_name="Building Number")
    apartment = models.CharField(max_length=64, verbose_name="Apartment Number", blank=True, null=True)

    def __str__(self):
        base = f"{self.street} {self.building}"
        if self.apartment:
            base += f", Apt {self.apartment}"
        full = f"{base}, {self.locality_name}, {self.state_or_province_name}, {self.country_name}"
        return full


class Company(models.Model):
    name = models.CharField(max_length=255, help_text="Legal or brand name of the company")
    slug = models.SlugField(unique=True, blank=True, help_text="URL-friendly identifier for the company")
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    established_year = models.IntegerField(null=True, blank=True, help_text="Year the company was founded")
    head = models.ForeignKey(
        'CommunityMember',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='headed_companies',
        help_text="Community member who leads the company"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Company.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Branch(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="branches",
        help_text="Main company this branch belongs to"
    )
    name = models.CharField(
        max_length=255,
        help_text="Name of the branch"
    )
    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Branch address"
    )
    head = models.ForeignKey(
        'CommunityMember',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='headed_branches',
        help_text="Community member who leads this branch"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {str(self.company)}"

    class Meta:
        verbose_name_plural = "Branches"


class CommunityMember(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='community_profile',
        help_text='Linked Django user account'
    )
    membership = models.ManyToManyField(
        Branch,
        related_name='members',
        help_text='Branches this member belongs to'
    )
    patron = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mentees',
        help_text='Another member who acts as a patron or mentor'
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.display_name


def generate_code(k=6):
    return ''.join(random.choices('0123456789', k=k))


class MembershipApplication(models.Model):
    email = models.EmailField(unique=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='applications',
        help_text="Branch this application is targeting"
    )
    code = models.CharField(
        max_length=10,
        unique=True,
        default=generate_code,
        help_text="Unique application code"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('endorsed', 'Endorsed'),
        ('invited', 'Invited'),
        ('rejected', 'Rejected'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Current status of the application"
    )

    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_verified(self):
        return self.verified_at is not None

    def __str__(self):
        return f"Application from {self.email} to {self.branch.name}"


class ReferenceRequest(models.Model):
    application = models.ForeignKey(
        MembershipApplication,
        on_delete=models.CASCADE,
        related_name='reference_requests',
        help_text="Membership application this reference request is linked to"
    )
    referrer = models.ForeignKey(
        CommunityMember,
        on_delete=models.CASCADE,
        related_name='sent_references',
        help_text="Community member who is endorsing the applicant"
    )
    message = models.TextField(
        blank=True,
        help_text="Optional message from the referrer about the applicant"
    )
    status_choices = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]
    status = models.CharField(
        max_length=20,
        choices=status_choices,
        default='pending',
        help_text="Status of the referral"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_accepted(self):
        return self.status == 'accepted'

    def __str__(self):
        return f"Reference by {self.referrer.display_name} for {self.application.email}"


class Post(models.Model):
    author = models.ForeignKey(
        CommunityMember,
        on_delete=models.CASCADE,
        related_name='posts',
        help_text='Community member who created the post'
    )
    content = models.TextField(
        help_text='Main text content of the post'
    )
    image = models.ImageField(
        upload_to='posts/',
        null=True,
        blank=True,
        help_text='Optional image attachment'
    )
    visibility_choices = [
        ('public', 'Public'),
        ('members', 'Members Only'),
        ('branch', 'Branch Only')
    ]
    visibility = models.CharField(
        max_length=20,
        choices=visibility_choices,
        default='members',
        help_text='Who can see this post'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Post by {self.author.display_name} on {self.created_at.strftime('%Y-%m-%d')}"
