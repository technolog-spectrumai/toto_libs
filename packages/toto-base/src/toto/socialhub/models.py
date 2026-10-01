import random
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
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


#: How many clearances a platform may have at once (2026-09-28, the owner's
#: rule): a clearance names what it opens — internal, confidential — and a
#: handful is the point. An eighth is refused.
MAX_CLEARANCES = 7
TOO_MANY_CLEARANCES = _(
    "A platform has at most %(max)d clearances. Remove one before making another.")

#: The mana pools a clearance may set a refill speed for (2026-09-28), one
#: ``regen_<pool>`` column each — the same three roles ``toto.mana`` names.
#: Written out here because socialhub does not depend on the mana app.
REGEN_POOLS = ("security", "compute", "storage")


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



class Clearance(models.Model):
    """What a person is trusted to read — ``internal``, ``confidential`` —
    and how fast their mana refills (2026-09-29; a Community with
    ``is_circle`` until then).

    A clearance is named after what it OPENS, never after who holds it, and
    it is the whole of the trust axis: an app that keeps something to
    clearances (``clearance_access``) reads it by ``Person.clearances``, and
    the hourly refill takes the fastest speed any of a person's clearances
    sets, pool by pool. It carries nothing else — no plan, no discount, no
    privilege, no page of its own: that is a Community's, and the two are
    orthogonal on purpose (README, "Communities and clearances"). Only a
    superuser makes one (the socialhub's Clearances tab, the admin, the
    console) or puts somebody in; members never see them listed.
    """

    name = models.CharField(_("name"), max_length=120, unique=True,
                            help_text=_("What it opens: internal, confidential."))
    slug = models.SlugField(unique=True, blank=True)
    regen_security = models.DecimalField(
        _("security mana per hour"), max_digits=12, decimal_places=4,
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text=_("Blank: the pool's own rate."))
    regen_compute = models.DecimalField(
        _("compute mana per hour"), max_digits=12, decimal_places=4,
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text=_("Blank: the pool's own rate."))
    regen_storage = models.DecimalField(
        _("storage mana per hour"), max_digits=12, decimal_places=4,
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text=_("Blank: the pool's own rate."))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("clearance")
        verbose_name_plural = _("clearances")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            # Cut to the column, leaving room for unique_slug's "-N": a long
            # name must not become a slug PostgreSQL refuses to store.
            room = self._meta.get_field("slug").max_length - 4
            self.slug = unique_slug(self, slugify(self.name)[:room].strip("-"), fallback="clearance")
        if self._would_exceed_the_cap():
            raise ValidationError({"name": TOO_MANY_CLEARANCES % {"max": MAX_CLEARANCES}})
        super().save(*args, **kwargs)

    def _would_exceed_the_cap(self) -> bool:
        """A new clearance is refused when seven exist; one that already
        counts is never refused its own save."""
        others = Clearance.objects.exclude(pk=self.pk) if self.pk else Clearance.objects.all()
        return others.count() >= MAX_CLEARANCES

    def clean(self):
        super().clean()
        if self._would_exceed_the_cap():
            raise ValidationError({"name": TOO_MANY_CLEARANCES % {"max": MAX_CLEARANCES}})

    def regen_speeds(self) -> dict:
        """``{pool: speed}`` for the pools this clearance sets."""
        return {pool: getattr(self, f"regen_{pool}") for pool in REGEN_POOLS
                if getattr(self, f"regen_{pool}") is not None}


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

    **A clearance grants nothing** (2026-09-28): clearances carry wiki reading and
    mana refill speed, and no rights. A row naming one is refused here (``clean`` and ``save``), the
    admin offers no inline for it, and ``privileges.has_privilege`` skips
    clearances should a row exist anyway — three layers, one rule.

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

    def save(self, *args, **kwargs):
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
    # A community only: a clearance is its own model (2026-09-29) and is given
    # by a superuser, never applied for.
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

    # The privacy notice the applicant accepted (2026-10-01, RODO): its number,
    # not a foreign key — a version is never deleted, and the number is what
    # its public address (socialhub:privacy_notice_version) is keyed by. Empty
    # on applications made before acceptance existed; nobody is asked again.
    privacy_version = models.PositiveIntegerField(null=True, blank=True)
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)

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

                # 5. The privacy notice accepted on the application goes with
                #    the person (2026-10-01): the application is housekeeping's
                #    to prune, the acceptance has to outlive it.
                if application.privacy_version:
                    PrivacyAcceptance.objects.get_or_create(
                        person=member, version=application.privacy_version,
                        defaults={"accepted_at": application.privacy_accepted_at
                                  or timezone.now()})


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


class PendingEmailChange(models.Model):
    """A member's request to move their account to a new e-mail address
    (My account, 2026-09-30), waiting for the link mailed to that address.

    The link carries a random token; only its SHA-256 is kept here, so a copy
    of this table cannot confirm anything. One row per member at most — a new
    request replaces the old — and the row is deleted the moment it is used,
    which is what makes the link single-use. Expiry is ``created`` plus
    ``toto.socialhub.email_change.LINK_HOURS``.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE,
                                related_name="pending_email_change")
    new_email = models.EmailField()
    #: The account's address when the link was asked for: the link acts only
    #: while the account still has it (review, 2026-10-01).
    old_email = models.EmailField(blank=True, default="")
    token_hash = models.CharField(max_length=64, unique=True)
    created = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "pending e-mail change"

    def __str__(self):
        return f"e-mail change for account {self.user_id}"


class PrivacyNotice(models.Model):
    """One version of the platform's privacy notice (2026-10-01, RODO).

    Here and not in ``toto.core``: the notice is what an applicant accepts on
    the socialhub's membership application (stage 35.2 records the version
    accepted, next to the application), and the socialhub is where a person
    first gives the platform their data. Core is the platform's scaffolding
    and asks nobody for anything.

    Versions are never edited: publishing writes a NEW row with the next
    number (``toto.socialhub.privacy.publish``), so the version somebody
    accepted stays readable, word for word, at its own address. Plain text in
    both languages, drawn escaped (``urlize`` then ``linebreaks``, the wiki's
    own chain) — never HTML from the database.
    """

    version = models.PositiveIntegerField(unique=True)
    text_pl = models.TextField()
    text_en = models.TextField()
    published_at = models.DateTimeField(default=timezone.now)
    # SET_NULL: erasing the account that published a version must not take
    # the version with it — applicants accepted that text.
    published_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    class Meta:
        ordering = ["-version"]
        verbose_name = "privacy notice"

    def __str__(self):
        return f"privacy notice v{self.version}"

    @classmethod
    def current(cls):
        return cls.objects.order_by("-version").first()

    def text_for(self, language: str | None) -> str:
        """The text in ``language`` (Polish for any ``pl…``, else English),
        the other one when that is empty."""
        polish = (language or "").lower().startswith("pl")
        first, second = (self.text_pl, self.text_en) if polish else (self.text_en, self.text_pl)
        return first or second


class PrivacyAcceptance(models.Model):
    """A person accepted one version of the privacy notice (2026-10-01).

    A table of its own, not fields on Person: ``toto.people`` knows nothing of
    the socialhub, and a later version accepted is a second row, not an
    overwrite of the first. Written when an applicant is admitted
    (``ReferenceRequest.save``), from what the application recorded; members
    who joined before acceptance existed have no row and are not asked for
    one (the owner's choice). CASCADE: it is the person's data and goes with
    them when the account is erased.
    """

    person = models.ForeignKey("people.Person", on_delete=models.CASCADE,
                               related_name="privacy_acceptances")
    version = models.PositiveIntegerField()
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-version"]
        verbose_name = "privacy notice acceptance"
        constraints = [models.UniqueConstraint(fields=["person", "version"],
                                               name="socialhub_privacy_acceptance_once")]

    def __str__(self):
        return f"{self.person} accepted privacy notice v{self.version}"


class DataExport(models.Model):
    """One *Download my data* request and how it went (2026-10-01, RODO).

    The row is written before the job is queued, so My account has a status
    to show from the first second; the worker builds the zip with
    ``toto.core.personal_data`` and files it in the member's personal bucket
    (``data_export.build``). One open export per member at a time — the
    database refuses a second (the constraint below), so two quick presses
    cannot queue two — and one a day (``data_export.INTERVAL``).

    ``output`` is SET_NULL: the member may delete the zip from their bucket,
    and the record that an export was made outlives it. CASCADE on the user:
    it is theirs, and goes when the account is erased.
    """

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, _("Queued")),
        (RUNNING, _("Being prepared")),
        (READY, _("Ready")),
        (FAILED, _("Failed")),
    ]
    OPEN = (PENDING, RUNNING)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_exports")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    output = models.ForeignKey("vault.VaultFile", on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="+")
    #: Rows per table and files included — what the README counts, for the page.
    summary = models.JSONField(default=dict, blank=True)
    #: One sentence for the member; the trace goes to the log, never here.
    error = models.CharField(max_length=300, blank=True)
    task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "data export"
        constraints = [models.UniqueConstraint(
            fields=["user"], condition=models.Q(status__in=["pending", "running"]),
            name="socialhub_one_open_data_export")]

    def __str__(self):
        return f"data export {self.pk} of {self.user_id} — {self.status}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN
