"""Electorates as data, and the register a vote freezes from them.

Stage 5 of the campaign: voting becomes self-contained. An **Electorate** is
a named, configurable roll — members and weights, equal or unequal — owned by
whatever scope configured it. **Voting rights** are membership; **voting
weight** is ballot power. The concept is the voting half of a cap table made
generic: members, weights, percentages — never "shares", which stay Business
Center's own vocabulary on its own registers.

A vote does not read an electorate live. When voting starts the membership
and weights are copied into **RollEntry** rows — the record date the company
register introduced, now generic — and standing is answered from that copy
for the life of the vote. Editing the electorate afterwards changes future
votes only.

**ConsensusProfile** (stage 7) is the named threshold a vote may select —
Normal Majority, Supermajority — configured by staff only. A vote snapshots
the profile's name AND percentage when it opens, so renaming or retuning a
profile can never rewrite what a past vote required.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class ElectorateQuerySet(models.QuerySet):
    def in_scope(self, scope_type: str = "", scope_id: str = ""):
        """Electorates belonging to exactly one scope — the ONLY door."""
        return self.filter(scope_type=scope_type, scope_id=str(scope_id or ""))

    def active(self):
        return self.filter(is_active=True)

    def for_person(self, person):
        """Every electorate this person sits on — ACROSS scopes.

        The visibility bug this fixes: the pages only ever asked for
        ``in_scope(SCOPE_GLOBAL)``, i.e. ``scope_type=""``, while every real
        electorate belongs to a company or a community. Members therefore saw
        an empty page while the admin showed their membership plainly. A
        person's electorates are not a property of one scope — they may sit
        on several bodies in several companies — so this deliberately does
        not filter by scope at all.
        """
        if person is None:
            return self.none()
        return self.filter(members__person=person).distinct()


class Electorate(models.Model):
    """A named roll: who may vote, and with what power."""

    class Kind(models.TextChoices):
        EQUAL = "equal", _("Equal — one member, one vote")
        WEIGHTED = "weighted", _("Weighted — members carry set weights")

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, blank=True)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices,
                            default=Kind.EQUAL)

    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="electorates_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ElectorateQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["scope_type", "scope_id", "slug"],
                                    name="uniq_electorate_slug_per_scope"),
        ]
        verbose_name = _("electorate")
        verbose_name_plural = _("electorates")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "electorate"
            slug, counter = base, 1
            siblings = Electorate.objects.in_scope(self.scope_type,
                                                   self.scope_id)
            while siblings.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f"{base}-{counter}"
            self.slug = slug
        super().save(*args, **kwargs)

    def total_weight(self) -> int:
        return self.members.aggregate(
            total=models.Sum("weight"))["total"] or 0

    def power_table(self) -> list:
        """Members with their share of the whole — the voting-power view."""
        total = self.total_weight()
        return [{
            "member": member,
            "name": member.display_name,
            "weight": member.weight,
            "percent": round(member.weight / total * 100, 1) if total else 0,
        } for member in self.members.select_related("person", "person__user")]


class ElectorateMember(models.Model):
    """A person and a weight. The person is optional.

    **Membership is a PERSON, not a login.** A Person is who somebody IS on
    this platform — the profile, the communities, the display name — while a
    User is only how they sign in. Keying membership on the login meant an
    electorate could not hold a member who has no account, and it meant every
    "which electorates am I in?" question had to travel through a join the
    domain does not actually have. A Person may sit on any number of
    electorates, in any number of companies or communities; nothing here
    assumes one default company.

    The person stays optional for the same reason the login used to be: a
    body may include an institution, an estate, or a member who only ever
    acts through counsel. They hold voting rights and weight, they count
    toward the electorate, and they act through a proxy (see
    :class:`Presence`).
    """

    electorate = models.ForeignKey(Electorate, on_delete=models.CASCADE,
                                   related_name="members")
    person = models.ForeignKey("people.Person", null=True, blank=True,
                               on_delete=models.CASCADE,
                               related_name="electorate_memberships")
    #: How this member is named when there is no person to name them by.
    label = models.CharField(max_length=150, blank=True)
    #: Ballot power. On an EQUAL electorate this stays 1 by convention; the
    #: snapshot copies whatever is written here, so the kind is documentation
    #: of intent, not a second enforcement path.
    weight = models.PositiveBigIntegerField(default=1)

    class Meta:
        ordering = ["-weight", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["electorate", "person"],
                                    condition=models.Q(person__isnull=False),
                                    name="uniq_electorate_member"),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.weight})"

    @property
    def user(self):
        """The login behind this member, or None.

        Ballots are still cast by a signed-in account, and the frozen
        register (:class:`RollEntry`) still records one — so the engine asks
        membership for its login through here rather than storing a second
        foreign key that could disagree with the person's.
        """
        return self.person.user if (self.person_id and self.person.user_id) else None

    @property
    def user_id(self):
        return self.person.user_id if self.person_id else None

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        if self.person_id:
            return self.person.display_name or str(self.person)
        return "—"


class Presence(models.TextChoices):
    """Whether a member of the register is at the session.

    Belonging to the electorate and being at the meeting are two different
    facts, and a governance instrument has to state both: a roll of a hundred
    with eleven in the room decided something rather different from a roll of
    a hundred with ninety.
    """

    PRESENT = "present", _("Present")
    REPRESENTED = "represented", _("Represented by proxy")
    ABSENT = "absent", _("Absent")


class RollEntry(models.Model):
    """One line of a vote's frozen register. Generic: a login, a display
    label, a weight — nothing about shares, seats or people-tables.

    ``user`` may be null: a register may name somebody with no account (the
    company case). They are ON the roll — counted in its size and so in the
    turnout denominator — and cannot cast.

    Attendance rides on the same row, snapshotted at the same moment: who
    BELONGS is the electorate's answer, who is PRESENT is the session's, and
    the two are recorded separately so a reader can see both.
    """

    question = models.ForeignKey("polls.Question", on_delete=models.CASCADE,
                                 related_name="roll")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    label = models.CharField(max_length=150, blank=True)
    weight = models.PositiveBigIntegerField(default=1)
    #: Taken when voting begins, with the register itself.
    presence = models.CharField(max_length=12, choices=Presence.choices,
                                default=Presence.PRESENT)
    #: Who holds the proxy, as a label — generic on purpose: a proxy may be
    #: another member, a lawyer or an institution, and none of those is a
    #: model this app should know about.
    represented_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-weight", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["question", "user"],
                                    condition=models.Q(user__isnull=False),
                                    name="uniq_roll_entry_user"),
        ]
        indexes = [
            models.Index(fields=["question", "user"]),
        ]
        verbose_name = _("roll entry")
        verbose_name_plural = _("roll entries")

    def __str__(self):
        return f"{self.label or self.user} ({self.weight})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "A register entry is a record date, not a live register.")
        super().save(*args, **kwargs)

    @property
    def is_represented(self) -> bool:
        return self.presence in (Presence.PRESENT, Presence.REPRESENTED)

    @property
    def can_act(self) -> bool:
        """Represented AND somebody able to cast: a login of their own, or a
        proxy named to act for them."""
        return self.is_represented and bool(self.user_id
                                            or self.represented_by)


class VoteExclusion(models.Model):
    """One member barred from ONE vote, with the reason on the record.

    Generic on purpose. Conflict of interest is the case everybody thinks of,
    but a body may also exclude somebody under a related-party rule, a
    suspension, or an interest declared in the minutes — so this stores a
    reason somebody wrote rather than a code from a list the platform
    invented. Hard-coding one legal scenario would make every other one a
    lie told in the nearest-fitting field.

    An exclusion is scoped to the vote and never touches the Electorate:
    membership is a standing fact, exclusion is a fact about one question.
    The excluded member stays visible in the procedure — a body's record
    should show who was barred and why, not quietly shorten the roll.
    """

    question = models.ForeignKey("polls.Question", on_delete=models.CASCADE,
                                 related_name="exclusions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="+")
    #: How the excluded member is named — matches the register's label, and
    #: carries the name for a member with no login.
    label = models.CharField(max_length=150, blank=True)
    #: Required. An exclusion with no reason is a silent disenfranchisement,
    #: and the whole point of recording one is that it can be read back.
    reason = models.TextField()
    excluded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    blank=True, on_delete=models.SET_NULL,
                                    related_name="vote_exclusions_made")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["question", "user"],
                                    condition=models.Q(user__isnull=False),
                                    name="uniq_vote_exclusion_user"),
        ]
        indexes = [models.Index(fields=["question", "user"])]
        verbose_name = _("voter exclusion")
        verbose_name_plural = _("voter exclusions")

    def __str__(self):
        return f"{self.label or self.user} — {self.reason[:60]}"

    def _decided(self) -> bool:
        from .models import Decision

        return Decision.objects.filter(question_id=self.question_id).exists()

    def save(self, *args, **kwargs):
        if not (self.reason or "").strip():
            raise ValueError("An exclusion must say why.")
        if self._decided():
            raise ValueError(
                "This vote is decided; its exclusions are part of the record.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Liftable while the vote is undecided — a mistake should be fixable —
        # and frozen the moment a decision exists.
        if self._decided():
            raise ValueError(
                "This vote is decided; its exclusions are part of the record.")
        return super().delete(*args, **kwargs)


class VoteProcedure(models.Model):
    """The session's own facts, snapshotted when voting begins.

    Three weights, because they answer three different questions and a
    procedural dispute is usually about which one somebody meant:

    ``electorate_weight``  everything on the register — the whole body.
    ``represented_weight`` present or held by proxy — the room.
    ``eligible_weight``    the part of the room that can actually cast.

    Written once, with the register. Notes stay editable until the decision
    is recorded — they carry facts observed during the session (an arrival,
    a departure, an objection), which by nature are not all known at the
    moment voting opens.
    """

    question = models.OneToOneField("polls.Question",
                                    on_delete=models.CASCADE,
                                    related_name="procedure")
    electorate_weight = models.PositiveBigIntegerField(default=0)
    represented_weight = models.PositiveBigIntegerField(default=0)
    eligible_weight = models.PositiveBigIntegerField(default=0)
    #: Represented, but barred from this question. Held out of
    #: ``eligible_weight`` and reported on its own, because "could not
    #: attend" and "was not allowed to vote" are different facts.
    excluded_weight = models.PositiveBigIntegerField(default=0)
    members_total = models.PositiveIntegerField(default=0)
    members_represented = models.PositiveIntegerField(default=0)
    members_excluded = models.PositiveIntegerField(default=0)
    frozen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("voting procedure")
        verbose_name_plural = _("voting procedures")

    def __str__(self):
        return f"{self.question_id}: {self.attendance_percent}% attending"

    @property
    def attendance_percent(self) -> float:
        if not self.electorate_weight:
            return 0.0
        return round(self.represented_weight / self.electorate_weight * 100, 1)

    def as_dict(self, *, notes: str = "") -> dict:
        return {
            "electorate_weight": self.electorate_weight,
            "represented_weight": self.represented_weight,
            "eligible_weight": self.eligible_weight,
            "excluded_weight": self.excluded_weight,
            "members_total": self.members_total,
            "members_represented": self.members_represented,
            "members_excluded": self.members_excluded,
            "attendance_percent": self.attendance_percent,
            "frozen_at": self.frozen_at.isoformat() if self.frozen_at else None,
            "notes": notes,
        }


class QuorumRule(models.Model):
    """A named attendance bar, staff-configured, snapshotted at vote open.

    Four modes, because bodies count a quorum four ways: not at all, as a
    fraction of the voting weight, as an absolute weight, or by somebody
    with authority saying so on the record. Evaluated from the vote's OWN
    snapshotted attendance — never from live rows.
    """

    class Mode(models.TextChoices):
        NONE = "none", _("No additional quorum")
        PERCENT = "percent", _("Minimum represented weight, percent")
        ABSOLUTE = "absolute", _("Minimum represented weight, absolute")
        MANUAL = "manual", _("Manual confirmation")

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices,
                            default=Mode.NONE)
    #: Percent of the electorate weight (PERCENT) or an absolute weight
    #: (ABSOLUTE). Meaningless — and null — for the other modes.
    threshold = models.DecimalField(max_digits=14, decimal_places=2,
                                    null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("quorum rule")
        verbose_name_plural = _("quorum rules")

    def __str__(self):
        if self.threshold is None:
            return self.name
        return f"{self.name} ({self.threshold})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ConveningMode(models.TextChoices):
    """How the session came to be entitled to decide anything.

    A formally convened meeting draws its authority from the notice that
    called it. A universal meeting draws it from everybody being in the room
    and nobody objecting — which is why that mode demands three explicit
    confirmations rather than a checkbox.
    """

    FORMAL = "formal", _("Formally convened")
    UNIVERSAL = "universal", _("100% represented / universal meeting")


#: The three facts a universal meeting must confirm, each on the record.
CONVENING_ASPECTS = (
    ("full_representation", _("The entire voting weight is represented")),
    ("no_objection_meeting", _("Nobody objects to holding the meeting")),
    ("no_objection_agenda", _("Nobody objects to the agenda")),
)


class ConsensusProfile(models.Model):
    """A named threshold, staff-configured, snapshotted at vote open.

    The percent is the bar a FOR share must STRICTLY exceed — the same
    comparison Business Center's constitution has always used.
    """

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["percent", "name"]
        verbose_name = _("consensus profile")
        verbose_name_plural = _("consensus profiles")

    def __str__(self):
        return f"{self.name} ({self.percent}%)"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
