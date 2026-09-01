import random
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils import timezone
from django.utils.text import Truncator
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.core.models import Federation
from toto.locations.models import Address, Territory
from toto.people.models import Person  # re-exported for backward compat  # noqa: F401
from toto.verbena.models import AbstractSection, AbstractTag
from toto.verbena.utils import unique_slug


class Community(DomainEntity):
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
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="headed_communities",
    )
    senior_members = models.ManyToManyField(
        "people.Person",
        related_name="senior_communities",
        blank=True,
        help_text="Members allowed to manage community announcements and news.",
    )

    logo = models.ImageField(
        upload_to='community_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )

    #: The community's governing document, as a PDF in the vault — its
    #: statute. SET_NULL and never CASCADE: deleting a file must not delete
    #: the community that adopted it (the texlab/aralia precedent). Every
    #: kind of community has one, not only companies; a reader reaches it
    #: through the vault's own download route, which enforces may_read.
    statute = models.ForeignKey(
        "vault.VaultFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The community's statute, as a PDF in the vault.",
    )

    is_autonomous = models.BooleanField(
        default=False,
        help_text="Marks this community as self-governing, with its own internal leadership and rules."
    )
    updated_at = models.DateTimeField(auto_now=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    is_foreign = models.BooleanField(default=False,
                                     help_text="Indicates whether this federation originates outside the local jurisdiction")
    is_federal_tribe = models.BooleanField(
        default=False,
        help_text=(
            "DEPRECATED as a live flag. It promised 'members are exempt from "
            "all poll taxes' for years; the poll tax that briefly existed (the "
            "head tax) has been removed, and what a community now does to what "
            "its members pay is a discount on a subscription plan — see "
            "toto.subscriptions.CommunityPlanDiscount. Kept because aurelian's "
            "mobilization app reads it by name for responder eligibility. "
            "Retiring it is an aurelian follow-up."
        ),
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

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        help_text="Parent community in the hierarchy",
    )

    federation = models.ForeignKey(
        Federation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="communities"
    )


class CommunityPrivilege(models.Model):
    """What one community grants its members. First-class, and ADMIN-ONLY.

    A community GRANTS; a person HOLDS the union across every community they
    belong to — **highest privilege always**. That is the granting mechanism,
    not a loophole: membership is invite-gated (``MembershipApplication`` →
    accepted → ``communities.add`` is the only door), so admitting someone to a
    community *is* the grant and expelling them *is* the revocation.

    **Rights are held by institutions, never by persons.** A community grants to
    every member, and membership is both the grant and the revocation: leave the
    community and the rights go with it. There is no way to give one named
    individual a right that their successor will not inherit, and that is the
    whole rule.

    There used to be a second institution here — ``Station``, a "Special Role"
    that fused an office, four ``may_*`` grants, a quota multiplier and a
    payslip into one row. It was removed in 8/2026 along with the treasury
    payroll that paid it; recurring payment is a Faucet now, and it pays people
    rather than posts.

    **The RIGHTS are never rendered outside Django admin.** No page shows or
    edits the ``may_*`` flags — not the community page, not the profile, not the
    metering pages. The gates that consume them simply work or refuse. Admin is
    the one editor, which is why this is its own model rather than booleans on
    ``Community``: one changelist of every grant on the platform, filterable and
    bulk-editable, instead of flags scattered through a 30-field community form.

    Every field here is admin-only. It used to carry one exception — a
    ``head_weight`` announced on the profile, because a member is entitled to
    know what their communities cost them. The head tax is gone and so is that
    field; what a community does to a member's bill is now a discount on a
    subscription plan, and it is announced on the plans page instead.

    A community without a row grants nothing — the commoner default, free to
    resolve.

    Each flag is honoured somewhere concrete — ``PRIVILEGES.md`` maps every
    field to the gate that reads it, and :mod:`toto.socialhub.privileges` is the
    ONE resolver everything asks.
    """

    community = models.OneToOneField(
        Community, on_delete=models.CASCADE, related_name="privilege")

    may_see_community_chain = models.BooleanField(
        default=False,
        help_text="Members may open the platform-wide community chain graph.",
    )
    may_administer_communities = models.BooleanField(
        default=False,
        help_text="Members may open the Administrata view of any community.",
    )
    may_manage_community_news = models.BooleanField(
        default=False,
        help_text=(
            "Members may publish news in ANY community, without being its "
            "head or a senior member."
        ),
    )
    may_operate_mint = models.BooleanField(
        default=False,
        help_text=(
            "Members may operate the mint on a host that IS the monetary "
            "master. This does not grant minting authority: the master check "
            "asks whether the MACHINE holds an issuer private key and takes "
            "no user argument, so on a branch host this still refuses."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["community__name"]

    def __str__(self):
        return f"privileges of {self.community.name}"


class CommunityNewsTopic(AbstractTag):
    class Meta:
        verbose_name = "Community news topic"
        verbose_name_plural = "Community news topics"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, fallback="topic")
        super().save(*args, **kwargs)


class CommunityNewsPost(AbstractSection):
    PUBLIC = "public"
    COMMUNITY = "community"

    VISIBILITY_CHOICES = [
        (PUBLIC, "Public"),
        (COMMUNITY, "Community"),
    ]

    author = models.ForeignKey(
        "people.Person",
        related_name="community_news_posts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    community = models.ForeignKey(
        Community,
        related_name="news_posts",
        on_delete=models.CASCADE,
    )
    topics = models.ManyToManyField(
        CommunityNewsTopic,
        related_name="posts",
        blank=True,
    )
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default=PUBLIC)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Community news post"
        verbose_name_plural = "Community news posts"

    @property
    def plain_text(self):
        return " ".join(strip_tags(self.content or "").split())

    @property
    def excerpt(self):
        return Truncator(self.plain_text).chars(220)

    @property
    def display_title(self):
        return self.title or self.excerpt or "Untitled post"

    def get_absolute_url(self):
        return f"{reverse('socialhub:community_detail', args=[self.community.slug])}#community-news-post-{self.pk}"

    def __str__(self):
        return self.display_title



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
    referrer = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="sent_references")
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

            # 1. Activate the user — looked up by their application email (the login
            #    username is chosen separately, so we must not match on username here).
            user = User.objects.filter(email=application.email).first()
            if user:
                user.is_active = True
                user.save(update_fields=["is_active"])

            # 2. Create Person if missing
            if user:
                member, created = Person.objects.get_or_create(
                    user=user,
                    defaults={
                        "display_name": user.username,
                        "email": user.email,
                    }
                )

                # 3. Add them to the community they applied to
                member.communities.add(application.community)

                # 4. The referrer becomes the patron, unless one is already
                #    set. Historically NOTHING wrote Person.patron — the
                #    person who actually vouched lived only on this row — so
                #    password recovery (sso_core.recovery) had to fall back to
                #    this table for every account. Writing it here closes that
                #    gap going forward; existing rows stay as they are and the
                #    fallback keeps covering them.
                if member.patron_id is None and self.referrer.pk != member.pk:
                    member.patron = self.referrer
                member.save()
