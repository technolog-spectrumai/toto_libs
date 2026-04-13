import random
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
from toto.locations.models import Address, Territory
from django.core.mail import EmailMessage, get_connection
from toto.gervazy.models import SecretPassword
from django.conf import settings
import uuid
from django.db import models
import os


class Community(models.Model):
    GUILD = "guild"
    COMPANY = "company"
    NON_PROFIT = "non_profit"
    FAMILY = "family"
    OTHER = "other"

    ORG_TYPES = [
        (GUILD, "Guild"),
        (COMPANY, "Company"),
        (NON_PROFIT, "Non-Profit"),
        (FAMILY, "Family"),
        (OTHER, "Other"),
    ]

    name = models.CharField(max_length=255, help_text="Name of the community or organization")
    slug = models.SlugField(unique=True, blank=True, help_text="URL-friendly identifier")

    org_type = models.CharField(
        max_length=20,
        choices=ORG_TYPES,
        default=OTHER,
        help_text="Type of organization"
    )

    location = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, related_name="community_locations")
    territory = models.ForeignKey(Territory, on_delete=models.SET_NULL, null=True, blank=True, related_name="community_territories")
    established_year = models.IntegerField(null=True, blank=True)

    head = models.ForeignKey(
        'CommunityMember',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='headed_communities',
        db_comment="headed_by"
    )

    logo = models.ImageField(
        upload_to='community_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )

    is_autonomous = models.BooleanField(
        default=False,
        help_text="Marks this community as self-governing, with its own internal leadership and rules."
    )
    updated_at = models.DateTimeField(auto_now=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    is_foreign = models.BooleanField(default=False,
                                     help_text="Indicates whether this federation originates outside the local jurisdiction")
    email_service = models.ForeignKey(
        "socialhub.EmailService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='community_emails',
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Community.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class CommunityMember(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='community_profile', null=True, blank=True)
    communities = models.ManyToManyField(Community, related_name='members', db_comment="member_of")
    patron = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mentees',
        db_comment="mentored_by"
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)
    slug = models.SlugField(unique=True, blank=True)
    # ➕ Add address field
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='residents',
        help_text="Optional address for this community member",
        db_comment="resides_at"
    )
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.display_name)
            slug = base_slug
            counter = 1
            while CommunityMember.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        # Prefer display_name
        if self.display_name:
            return self.display_name

        # Fallback to linked Django user if present
        if self.user:
            return self.user.get_full_name() or self.user.username

        return "Unnamed Member"

def generate_code(k=6):
    return ''.join(random.choices('0123456789', k=k))


class MembershipApplication(models.Model):
    email = models.EmailField(unique=True)
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='applications')
    code = models.CharField(max_length=10, unique=True, default=generate_code)
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_verified(self):
        return self.verified_at is not None

    def __str__(self):
        return f"Application from {self.email} to {self.community.name}"


class ReferenceRequest(models.Model):
    application = models.ForeignKey(MembershipApplication, on_delete=models.CASCADE, related_name='reference_requests')
    referrer = models.ForeignKey(CommunityMember, on_delete=models.CASCADE, related_name='sent_references')
    message = models.TextField(blank=True)
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_accepted(self):
        return self.status == 'accepted'

    def __str__(self):
        return f"Reference by {self.referrer.display_name} for {self.application.email}"

    def save(self, *args, **kwargs):
        status_changed_to_accepted = False

        if self.pk:
            old = ReferenceRequest.objects.get(pk=self.pk)
            if old.status != "accepted" and self.status == "accepted":
                status_changed_to_accepted = True

        super().save(*args, **kwargs)

        if status_changed_to_accepted:
            application = self.application

            # 1. Activate the user
            try:
                user = User.objects.get(username=application.email)
                user.is_active = True
                user.save()
            except User.DoesNotExist:
                user = None

            # 2. Create CommunityMember if missing
            if user:
                member, created = CommunityMember.objects.get_or_create(
                    user=user,
                    defaults={
                        "display_name": user.username,
                        "email": user.email,
                    }
                )

                # 3. Add them to the community they applied to
                member.communities.add(application.community)
                member.save()


class EmailService(models.Model):
    """
    Stores SMTP configuration for a system component.
    The SMTP password is stored encrypted inside a SecretPassword instance.
    This model does NOT modify or manage the password itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Optional human-friendly name
    name = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Optional name for this email service. Auto-generated if omitted."
    )
    pass_phrase_env_var = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Environment variable to query for passphrase"
    )

    email_address = models.EmailField(
        help_text="SMTP login email address"
    )

    # Encrypted SMTP password (managed by SecretPassword)
    secret_password = models.OneToOneField(
        SecretPassword,
        on_delete=models.CASCADE,
        related_name="email_service_for",
        help_text="Encrypted SMTP password stored in SecretPassword"
    )

    host = models.CharField(max_length=255, help_text="SMTP server hostname")
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    # ---------------------------------------------------------
    # Save override
    # ---------------------------------------------------------

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = f"email-service-{uuid.uuid4()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"EmailService {self.name} ({self.email_address})"

    # ---------------------------------------------------------
    # Email sending helper
    # ---------------------------------------------------------

    def send_email(self, subject, body, to, html=None):
        """
        Sends an email using this EmailService's SMTP configuration.
        Passphrase is taken from Django settings.
        """

        passphrase = os.environ.get(self.pass_phrase_env_var, "qwerty")
        if not passphrase:
            raise RuntimeError(f"Environment variable {self.pass_phrase_env_var} must be set.")

        password = self.secret_password.get_password(passphrase)

        connection = get_connection(
            backend=settings.EMAIL_BACKEND,
            host=self.host,
            port=self.port,
            username=self.email_address,
            password=password,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=self.email_address,
            to=[to] if isinstance(to, str) else to,
            connection=connection,
        )

        if html:
            msg.content_subtype = "html"
            msg.body = html

        return msg.send()


