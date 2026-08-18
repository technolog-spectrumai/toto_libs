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
    every member; a :class:`Station` grants to whoever currently holds it. A
    person's rights are the union of the communities they belong to and the
    offices they hold, and both are revoked the same way — leave the community,
    or vacate the office. There is no way to give one named individual a right
    that their successor will not inherit, and that is the whole rule.

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


class Station(models.Model):
    """A federal office — persistent, named, and larger than whoever holds it.

    The office exists whether or not anyone holds it; holders change, the office
    does not. Where a community grants to all its members, a station grants to
    its one current holder, and vacating it revokes exactly as leaving a
    community does. Both are institutions; neither is a person.

    **Every station is federal.** ``serves`` records which community an office
    works FOR, never who pays it: the federal treasury pays every stipend, is
    appointed from Django admin, and lists every office on one roster. A
    community that paid its own officers would be a community with its own
    budget, its own payroll and its own loyalty — so the design does not offer
    that, and a community that wants an office funded asks the federation for
    it, informally, and an admin creates one here.

    **Membership is what qualifies a holder.** An office serving a community is
    held by somebody who belongs to it — the rule that replaced a platform-wide
    "committed citizen" status, which was computed from signatures on a
    governing document and retired with it.

    **The roster is public; the capabilities are not.** Which offices exist, what
    they are for and who holds them are the point of an institution and render
    freely. What an office GRANTS stays in admin, exactly as
    :class:`CommunityPrivilege`'s rights do. What it PAYS stays in admin too,
    with one exception: a holder sees their own stipend on their own profile,
    and nobody else's — the profile page shows any person to any logged-in user,
    so that line is gated on being its owner.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True, blank=True)
    charter = models.TextField(
        blank=True,
        help_text="What this office is responsible for. Shown on the public roster.",
    )
    holder = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        # No reverse accessor. `person.stations` and `community.stations` now
        # belong to toto.stations, the app that replaced this model's only
        # load-bearing part. Nothing ever read either reverse here — verified by
        # a full sweep across every host — so this frees the name at no cost.
        related_name="+",
        help_text="Empty means VACANT: the office keeps existing and pays nobody.",
    )
    serves = models.ForeignKey(
        Community, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
        help_text=(
            "Which community this office works for, if any. Attribution only — "
            "the federal treasury pays every special role and a community "
            "never pays anyone."
        ),
    )
    since = models.DateField(
        null=True, blank=True,
        help_text="When the current holder took office.",
    )
    active = models.BooleanField(default=True)

    # ---- capabilities: ADMIN-ONLY, rendered nowhere -----------------------
    may_see_community_chain = models.BooleanField(default=False)
    may_administer_communities = models.BooleanField(default=False)
    may_manage_community_news = models.BooleanField(default=False)
    may_operate_mint = models.BooleanField(default=False)

    limit_multiplier = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("1"),
        help_text=(
            "Multiplies every quota limit for the holder, so an office has the "
            "headroom its work needs. HEADROOM ONLY: prices "
            "are untouched, and a holder pays exactly what anyone else pays "
            "for the same action."
        ),
    )
    stipend = models.DecimalField(
        max_digits=30, decimal_places=10, default=Decimal("0"),
        help_text=(
            "Paid per period by the federal treasury, in the host's billing "
            "asset. 0 is an unpaid office, which is an ordinary thing to be."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["serves__name", "name"]
        verbose_name = "Special Role"
        verbose_name_plural = "Special Roles"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "station"
            slug, n = base, 1
            while Station.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.holder_id is None:
            return
        # A paid office must be reachable from a billing account. Person.user is
        # nullable and nothing creates a Person on signup, so a user-less Person
        # is an ordinary row here — and paying one is impossible rather than
        # merely awkward: there is no account to credit.
        if self.stipend and not self.holder.user_id:
            raise ValidationError({
                "holder": "A paid office needs a holder with a login — there is "
                          "no account to pay otherwise.",
            })
        # Membership is the qualification. It replaced a platform-wide
        # citizenship test computed from signatures on a governing document;
        # the membership register is what actually records who belongs.
        # An office that SERVES a community is held by one of its members;
        # a purely federal office (serves is nullable — "if any") asks only
        # that the holder belong somewhere.
        if self.serves_id:
            if not self.holder.communities.filter(pk=self.serves_id).exists():
                raise ValidationError({
                    "holder": "An office is held by a member of the community "
                              "it serves — this person is not one yet.",
                })
        elif not self.holder.communities.exists():
            raise ValidationError({
                "holder": "An office is held by somebody who belongs to a "
                          "community — this person belongs to none.",
            })


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
                member.save()
