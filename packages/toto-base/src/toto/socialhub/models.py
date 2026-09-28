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
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from toto.core.domain import DomainEntity
from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.core.models import Federation
from toto.locations.models import Address, Territory
from toto.people.models import Person  # re-exported for backward compat  # noqa: F401
from toto.verbena.models import AbstractSection, AbstractTag
from toto.verbena.utils import unique_slug


#: What the two axes refuse each other (2026-09-28), worded once — see
#: ``Community.is_circle`` and the README, "Functional communities and circles".
CIRCLE_NOT_JOINABLE = _(
    "A circle is not joined by application: a superuser adds its members.")
CIRCLE_GRANTS_NO_PRIVILEGE = _(
    "A circle grants no privileges: it decides who reads, never what anybody may do.")
CIRCLE_CARRIES_NOTHING = _(
    "A circle carries no plan offers, discounts or privileges: remove this "
    "community's before making it a circle.")
CIRCLE_STANDS_ALONE = _(
    "A circle stands alone: it has no parent and is no community's parent.")

#: The rows that put a community on the money axis: its privilege here and,
#: where ``toto.subscriptions`` is installed, its plan offers and discount.
#: Reverse accessor names rather than imports — socialhub does not depend on
#: subscriptions, and a relation this host does not have is simply skipped.
MONEY_AXIS_RELATIONS = ("privilege", "plan_offers", "subscription_discount")


class CommunityQuerySet(models.QuerySet):
    """The two kinds of community, told apart in one place (2026-09-28).

    Every query that serves one axis names its kind: the money axis asks for
    ``functional()``, the reading axis for ``circles()``, and a page listing
    communities to a person asks ``listed_for(user)``.
    """

    def functional(self):
        """Communities that carry plans, offers, discounts and privileges."""
        return self.filter(is_circle=False)

    def circles(self):
        """Communities that carry wiki reading, and nothing else."""
        return self.filter(is_circle=True)

    def listed_for(self, user):
        """What ``user`` may see listed or open: everything for a superuser,
        the functional communities for anybody else. A circle is hidden from
        members wherever communities are shown — the directory, a profile, the
        map, the API — and its page answers 404, as a missing one does."""
        if getattr(user, "is_superuser", False):
            return self
        return self.functional()


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
    #: A CIRCLE (2026-09-28) — ``seniors``, ``newcomers``, ``board`` — decides
    #: who may READ a wiki page, and nothing else. A functional community —
    #: ``devs``, ``testers`` — carries plan offers, discounts and privileges,
    #: and never reading. Orthogonal on purpose: one membership list,
    #: ``Person.communities``, serves both axes, and each axis refuses the
    #: other in several layers (README, "Functional communities and circles").
    #: A circle is hidden from members, joined only through the admin, and
    #: read by direct membership — it has no parent and is nobody's.
    is_circle = models.BooleanField(
        pgettext_lazy("community kind", "circle"),
        default=False,
        db_index=True,
        help_text=_(
            "A circle decides who may read wiki pages, and nothing else: no "
            "plan offers, no discounts, no privileges. Hidden from members; "
            "a superuser adds its members."
        ),
    )

    objects = CommunityQuerySet.as_manager()

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

    def clean(self):
        """A community changes axis only when nothing of the other one holds it.

        Making a community a circle is refused while it carries a plan offer,
        a discount or a privilege: the money axis would quietly stop honouring
        them (every resolver skips circles), and its members would lose a plan
        on their next request. A circle has no parent and is no parent: read
        by direct membership, a tree would promise an inheritance that is not
        there.
        """
        super().clean()
        if self.is_circle and self.carries_the_money_axis():
            raise ValidationError({"is_circle": CIRCLE_CARRIES_NOTHING})
        if self.is_circle and self.parent_id:
            raise ValidationError({"parent": CIRCLE_STANDS_ALONE})
        if self.is_circle and self.pk and self.children.exists():
            raise ValidationError({"is_circle": CIRCLE_STANDS_ALONE})
        if self.parent_id and Community.objects.circles().filter(pk=self.parent_id).exists():
            raise ValidationError({"parent": CIRCLE_STANDS_ALONE})

    def carries_the_money_axis(self) -> bool:
        """Whether a privilege, a plan offer or a discount names this community."""
        if not self.pk:
            return False
        for relation in type(self)._meta.related_objects:
            if relation.get_accessor_name() not in MONEY_AXIS_RELATIONS:
                continue
            rows = relation.related_model._default_manager.filter(
                **{relation.field.name: self.pk})
            if rows.exists():
                return True
        return False

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

    **A circle grants nothing** (2026-09-28): circles carry wiki reading and no
    rights. A row naming one is refused here (``clean`` and ``save``), the
    admin offers no inline for it, and ``privileges.has_privilege`` skips
    circles should a row exist anyway — three layers, one rule.

    Each flag is honoured somewhere concrete — ``PRIVILEGES.md`` maps every
    field to the gate that reads it, and :mod:`toto.socialhub.privileges` is the
    ONE resolver everything asks.
    """

    community = models.OneToOneField(
        Community, on_delete=models.CASCADE, related_name="privilege",
        limit_choices_to={"is_circle": False})

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

    def _refuse_a_circle(self):
        if self.community_id and Community.objects.circles().filter(
                pk=self.community_id).exists():
            raise ValidationError({"community": CIRCLE_GRANTS_NO_PRIVILEGE})

    def clean(self):
        super().clean()
        self._refuse_a_circle()

    def save(self, *args, **kwargs):
        self._refuse_a_circle()
        super().save(*args, **kwargs)


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
    # A functional community only: a circle is joined through the admin, never
    # by application (2026-09-28). The form offers none, `clean` refuses one,
    # and accepting a reference refuses it again (ReferenceRequest.save).
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='applications',
                                  limit_choices_to={"is_circle": False})
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

    def clean(self):
        super().clean()
        if self.community_id and Community.objects.circles().filter(pk=self.community_id).exists():
            raise ValidationError({"community": CIRCLE_NOT_JOINABLE})

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

    def _applies_to_a_circle(self) -> bool:
        return bool(self.application_id and Community.objects.circles().filter(
            applications=self.application_id).exists())

    def clean(self):
        super().clean()
        if self.status == "accepted" and self._applies_to_a_circle():
            raise ValidationError(CIRCLE_NOT_JOINABLE)

    def save(self, *args, **kwargs):
        status_changed_to_accepted = False

        if self.pk:
            old = ReferenceRequest.objects.get(pk=self.pk)
            if old.status != "accepted" and self.status == "accepted":
                status_changed_to_accepted = True

        # Accepting is the one door a member walks through into a community,
        # and a circle is never behind it: refused before anything is written,
        # whoever calls — the view, the admin or a script.
        if status_changed_to_accepted and self._applies_to_a_circle():
            raise ValidationError(CIRCLE_NOT_JOINABLE)

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


class CommunityForum(models.Model):
    """The ONE room a community talks in.

    A SLUG, NOT A FOREIGN KEY — `company.CompanyForum` explains this at length
    and the reasoning is identical here, only the packages differ. `toto.forum`
    ships in toto-chat; socialhub is toto-base, which cannot depend on it (an
    FK *string* counts as a hard edge to `scripts/check_package_graph.py`). So
    the room is named by slug and resolved when the page renders, and a host
    with no chat app simply shows no link rather than failing to import.

    WHAT THAT COSTS: deleting the room leaves this row pointing at nothing.
    `channel()` returns None and the panel renders a "no room yet" state, so
    the failure is a missing link and not a broken one — and remaking a room
    with that slug restores it, which a cascade-deleted row would not allow.

    This REPLACES the community news panel. News was a second, thinner
    publishing surface next to a forum this platform already runs: two places
    to post, one of which had no replies, no moderation and no notifications.
    The posts and their model are untouched — the plugin that showed them is
    what went — so nothing is lost and a community that wants a feed uses the
    room it already has.
    """

    community = models.OneToOneField(
        "socialhub.Community", on_delete=models.CASCADE, related_name="forum",
        help_text="The community whose room this is.")
    channel_slug = models.SlugField(
        max_length=50, unique=True,
        help_text="The forum room's slug. Resolved when the page renders.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "Community forum room"
        verbose_name_plural = "Community forum rooms"

    def __str__(self):
        return f"{self.community} → {self.channel_slug}"

    def channel(self):
        """The room, or None when chat is not installed or it is gone.

        Never raises: a community page must render on a host with no forum at
        all, which is exactly the case an FK could not express.
        """
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.forum"):
            return None
        try:
            model = django_apps.get_model("forum", "ForumChannel")
            return model.objects.filter(slug=self.channel_slug).first()
        except Exception:  # noqa: BLE001 — a missing room is not an error here
            return None
