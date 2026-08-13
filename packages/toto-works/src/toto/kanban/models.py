from collections import defaultdict

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from toto.core.domain import DomainEntity
from toto.people.models import Person
from toto.verbena.models import AbstractPage


THREE_SCALE = [
    (1, "Low"),
    (2, "Medium"),
    (3, "High"),
]

FIB_SCALE = [
    (1, "Tiny"),
    (2, "Small"),
    (3, "Medium"),
    (5, "Big"),
    (8, "Large"),
]


class TaskStatus(models.TextChoices):
    """The only three places a task can be.

    These were rows in a ``Column`` table until v1.15, one set per project and
    seeded differently by every seeder — so "done" meant "the column with the
    highest position", and adding a column after Done silently moved the
    reviewer gate with it.
    """

    TODO = "todo", _("To do")
    IN_PROGRESS = "in_progress", _("In progress")
    DONE = "done", _("Done")


#: Board order, left to right. Promote and demote are index arithmetic on this.
STATUS_ORDER = (TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.DONE)


def adjacent_status(status, direction):
    """The status one step forward or back, or None at either end."""
    try:
        index = STATUS_ORDER.index(status)
    except ValueError:
        index = 0
    target = index + (1 if direction == "next" else -1)
    return STATUS_ORDER[target] if 0 <= target < len(STATUS_ORDER) else None


class TaskQuerySet(models.QuerySet):
    def in_board_order(self):
        """Order by board position, not alphabetically.

        ``ORDER BY status`` sorts done < in_progress < todo — exactly backwards.
        Anything presenting tasks in flow order has to annotate a rank first.
        """
        rank = models.Case(
            *[models.When(status=value, then=index) for index, value in enumerate(STATUS_ORDER)],
            output_field=models.IntegerField(),
        )
        return self.annotate(_status_rank=rank).order_by("_status_rank", "position", "pk")


class Project(DomainEntity):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    project_lead = models.ForeignKey(Person, on_delete=models.CASCADE)
    auditors = models.ManyToManyField(
        "Practitioner",
        related_name="audited_projects",
        blank=True,
        help_text="Practitioners who may move tasks between states.",
    )

    def __str__(self):
        return self.name


class Campaign(DomainEntity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    zone = models.ForeignKey(
        "locations.Zone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )

    def __str__(self):
        return self.name


class MissionVisibility(models.TextChoices):
    #: Anyone who can see the project sees the mission — the status quo, and the
    #: default, so nothing disappears when the column arrives.
    PROJECT = "project", _("Project")
    #: Owner, the people listed on visible_to, the project lead, project
    #: auditors, and staff. Everyone else gets a 404, not a 403 — a private
    #: mission's existence must not leak.
    PRIVATE = "private", _("Private")


class Mission(DomainEntity):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="missions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    urgency = models.IntegerField(choices=THREE_SCALE, default=2)
    impact = models.IntegerField(choices=THREE_SCALE, default=2)
    visibility = models.CharField(
        max_length=10,
        choices=MissionVisibility.choices,
        default=MissionVisibility.PROJECT,
        db_index=True,
    )
    visible_to = models.ManyToManyField(
        Person,
        blank=True,
        related_name="visible_missions",
        help_text="People who can always see this mission when it is private.",
    )
    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
    )
    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
    )
    zone = models.ForeignKey(
        "locations.Zone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
        help_text="Overrides the campaign's zone; must lie inside it.",
    )
    calendar_event = models.ForeignKey(
        "events.ScheduledEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kanban_missions",
    )
    owner = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} ({self.campaign.name})"

    @property
    def urgency_label(self):
        return dict(THREE_SCALE).get(self.urgency, self.urgency)

    @property
    def impact_label(self):
        return dict(THREE_SCALE).get(self.impact, self.impact)

    @property
    def effective_zone(self):
        return self.zone or self.campaign.zone

    def user_can_read(self, user):
        """Delegates to the queryset helper so the two can never drift apart."""
        return visible_missions_for(user, Mission.objects.filter(pk=self.pk)).exists()

    def clean(self):
        self._validate_zone_containment()

    def _validate_zone_containment(self):
        """Refuse a mission zone that lies outside its campaign's zone.

        Runs through every form and admin path via full_clean, matching how
        Task.clean guards the sprint-project rule — deliberately not a save()
        hook, which would abort restores and blow up on unrelated saves after
        someone edits a zone's geometry.

        Skips silently when there is nothing to compare: no mission zone, no
        campaign yet, GIS off (the geometry field does not exist on that
        build), no campaign zone, same zone on both, or a missing geometry.
        """
        if not (self.zone_id and self.campaign_id):
            return
        if not getattr(settings, "HAS_GIS", True):
            return
        campaign_zone = self.campaign.zone
        if campaign_zone is None or campaign_zone.pk == self.zone_id:
            return
        inner = getattr(self.zone, "geometry", None)
        outer = getattr(campaign_zone, "geometry", None)
        if inner is None or outer is None:
            return
        # covers(), not contains(): contains is false for a zone sharing an
        # edge with its parent, and a district on the border is still inside.
        if not outer.covers(inner):
            raise ValidationError({
                "zone": _(
                    "Mission zone must lie inside the campaign's zone (%(zone)s)."
                ) % {"zone": campaign_zone.name},
            })


def _mission_visibility_q(user, prefix=""):
    """Q selecting missions `user` may see; prefix joins from another model,
    e.g. prefix="mission__" when filtering Task rows."""
    p = prefix
    return (
        Q(**{f"{p}visibility": MissionVisibility.PROJECT})
        | Q(**{f"{p}owner__user": user})
        | Q(**{f"{p}visible_to__user": user})
        | Q(**{f"{p}campaign__project__project_lead__user": user})
        | Q(**{f"{p}campaign__project__auditors__person__user": user})
    )


def visible_missions_for(user, base_qs=None):
    """Missions `user` may see.

    Lives here rather than beside the permission helpers in views.py because
    the metrics calculators need it and views.py imports metrics.py.
    """
    qs = base_qs if base_qs is not None else Mission.objects.all()
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.is_staff or user.is_superuser:
        return qs
    # distinct() is load-bearing: two M2M joins would duplicate rows, and the
    # metrics calculator counts rows.
    return qs.filter(_mission_visibility_q(user)).distinct()


def visible_tasks_for(user, base_qs=None):
    """Tasks whose mission `user` may see."""
    qs = base_qs if base_qs is not None else Task.objects.all()
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.is_staff or user.is_superuser:
        return qs
    return qs.filter(_mission_visibility_q(user, prefix="mission__")).distinct()


class Sprint(DomainEntity):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    def __str__(self):
        return self.name


class Practitioner(DomainEntity):
    """A professional profile for a Person, independent of any specific project."""

    ROLE_CONTRIBUTOR = "contributor"
    ROLE_REVIEWER = "reviewer"
    ROLE_AUDITOR = "auditor"
    ROLE_MANAGER = "manager"
    ROLE_OBSERVER = "observer"

    ROLE_CHOICES = [
        (ROLE_CONTRIBUTOR, "Contributor"),
        (ROLE_REVIEWER, "Reviewer"),
        (ROLE_AUDITOR, "Auditor"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_OBSERVER, "Observer"),
    ]

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="practitioner_profiles")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CONTRIBUTOR)
    is_active = models.BooleanField(default=True)
    work_description = models.TextField(
        blank=True,
        help_text="Free-text description of this practitioner's role or services in the project.",
    )
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.person} ({self.role})"


class ProjectCommitment(models.Model):
    """Links a Practitioner to a Project and tracks their time commitment."""

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="commitments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="commitments")
    hours_per_day = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        help_text="Number of hours per day committed to this project.",
    )
    is_active = models.BooleanField(default=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["practitioner", "project"], name="unique_commitment_per_project"),
        ]

    def __str__(self):
        return f"{self.practitioner} → {self.project} ({self.hours_per_day}h/day)"


class Task(DomainEntity):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="tasks")
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.TODO,
        db_index=True,
    )
    sprint = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(
        Practitioner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    reviewer = models.ForeignKey(
        Practitioner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_tasks",
        help_text="Optional reviewer who signs off the task before it is completed.",
    )
    due_date = models.DateField(null=True, blank=True)
    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        # "tasks" on Address is not taken, but kanban_tasks mirrors
        # kanban_missions on ScheduledEvent and keeps the origin obvious.
        related_name="kanban_tasks",
    )
    calendar_event = models.ForeignKey(
        "events.ScheduledEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kanban_tasks",
    )
    position = models.PositiveIntegerField(default=0)
    weight = models.IntegerField(choices=FIB_SCALE, default=1)
    metadata = models.JSONField(blank=True, null=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        constraints = [
            # Every metric in this app keys on completed_at while the board reads
            # status. Before this constraint the two could disagree — and did:
            # only the HTML promote/demote views maintained the timestamp, so any
            # task moved through the JSON API was done on the board and open in
            # every chart. save() below keeps them in step; this is what makes it
            # true for the paths save() never sees (QuerySet.update, bulk_update,
            # loaddata, data migrations).
            models.CheckConstraint(
                check=(
                    models.Q(status=TaskStatus.DONE, completed_at__isnull=False)
                    | (~models.Q(status=TaskStatus.DONE) & models.Q(completed_at__isnull=True))
                ),
                name="kanban_task_completed_at_matches_status",
            ),
        ]
        indexes = [
            models.Index(fields=["mission", "status"], name="kanban_task_mission_status"),
        ]

    def __str__(self):
        return self.title

    @property
    def weight_label(self):
        return dict(FIB_SCALE).get(self.weight, self.weight)

    @property
    def is_done(self):
        return self.status == TaskStatus.DONE

    def open_blockers(self):
        """Tasks that block this one and are not finished.

        Both conditions belong in one filter() call: split across two they would
        match different relation rows and over-report.
        """
        return Task.objects.filter(
            outgoing_relations__to_task=self,
            outgoing_relations__relation_type=RelationType.BLOCKS,
        ).exclude(status=TaskStatus.DONE)

    def relations(self):
        """Every relation touching this task, each with the label to show here.

        Two traversals rather than a UNION: a combined queryset cannot
        select_related a different side per branch, and Django forbids most
        operations on one afterwards.

        Plain ``.all()`` so a caller listing many tasks can prefetch
        ``incoming_relations__from_task`` and ``outgoing_relations__to_task``
        and pay nothing here. Adding select_related would bypass that cache and
        re-query once per task.
        """
        edges = []
        for relation in self.outgoing_relations.all():
            edges.append({
                "relation": relation,
                "other": relation.to_task,
                "label": relation.get_relation_type_display(),
                "is_reverse": False,
            })
        for relation in self.incoming_relations.all():
            edges.append({
                "relation": relation,
                "other": relation.from_task,
                "label": INVERSE_LABELS[relation.relation_type],
                "is_reverse": True,
            })
        return edges

    def save(self, *args, update_fields=None, **kwargs):
        """Keep completed_at derived from status.

        The update_fields widening is not optional: every caller in this app
        saves with update_fields, so without it the timestamp would be corrected
        in memory and never written — which is the exact bug this invariant
        exists to kill, reintroduced invisibly.
        """
        if self.status == TaskStatus.DONE:
            if self.completed_at is None:
                self.completed_at = timezone.now()
        elif self.completed_at is not None:
            self.completed_at = None

        if update_fields is not None and "status" in set(update_fields):
            update_fields = {*update_fields, "completed_at"}

        super().save(*args, update_fields=update_fields, **kwargs)

    def clean(self):
        if not self.mission_id:
            return
        try:
            project = self.mission.campaign.project
        except (Mission.DoesNotExist, Campaign.DoesNotExist, Project.DoesNotExist):
            return

        if self.sprint_id and self.sprint.project_id != project.pk:
            raise ValidationError({"sprint": "Sprint must belong to the same project as the task."})


class RelationType(models.TextChoices):
    BLOCKS = "blocks", _("blocks")
    PRECEDES = "precedes", _("precedes")
    RELATES = "relates", _("relates to")
    DUPLICATES = "duplicates", _("duplicates")
    TESTS = "tests", _("tests")
    IMPLEMENTS = "implements", _("implements")


#: What the relation reads as from the other end. One row carries both readings,
#: so "A blocks B" shows as "is blocked by A" on B without a second row.
INVERSE_LABELS = {
    RelationType.BLOCKS: _("is blocked by"),
    RelationType.PRECEDES: _("follows"),
    RelationType.RELATES: _("relates to"),
    RelationType.DUPLICATES: _("is duplicated by"),
    RelationType.TESTS: _("is tested by"),
    RelationType.IMPLEMENTS: _("is implemented by"),
}

#: Reads the same in both directions, so the stored direction is arbitrary.
SYMMETRIC_RELATIONS = frozenset({RelationType.RELATES})

#: Types that impose an order. A cycle across any mix of these is nonsense.
BLOCKING_RELATIONS = frozenset({RelationType.BLOCKS, RelationType.PRECEDES})


class TaskRelation(DomainEntity):
    """A typed edge between two tasks in the same campaign.

    Stored once per edge. The forward label renders on ``from_task`` and the
    inverse on ``to_task``, so a mirrored row would be a second copy of one fact
    that nothing keeps in step.
    """

    from_task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="outgoing_relations")
    to_task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="incoming_relations")
    relation_type = models.CharField(max_length=20, choices=RelationType.choices)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        Practitioner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("relation_type", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=["from_task", "to_task", "relation_type"],
                name="unique_task_relation",
            ),
            models.CheckConstraint(
                check=~models.Q(from_task=models.F("to_task")),
                name="task_relation_not_self",
            ),
        ]
        indexes = [
            # The unique constraint's own index already leads with from_task.
            # This covers the other direction, which is the hot one: the blocker
            # badge asks "what blocks me?".
            models.Index(fields=["to_task", "relation_type"], name="taskrel_to_type_idx"),
        ]

    def __str__(self):
        return f"{self.from_task} {self.get_relation_type_display()} {self.to_task}"

    def _canonicalize(self):
        """Order the endpoints of a symmetric relation so the mirror collides.

        With a fixed direction, the unique constraint rejects the reverse
        duplicate for free — no functional index, no second query.
        """
        if self.relation_type not in SYMMETRIC_RELATIONS:
            return
        if not (self.from_task_id and self.to_task_id):
            return
        if self.from_task_id <= self.to_task_id:
            return
        self.from_task_id, self.to_task_id = self.to_task_id, self.from_task_id
        self._state.fields_cache.pop("from_task", None)
        self._state.fields_cache.pop("to_task", None)

    def clean(self):
        if not (self.from_task_id and self.to_task_id):
            return
        if self.from_task_id == self.to_task_id:
            raise ValidationError(_("A task cannot relate to itself."))

        campaigns = dict(
            Task.objects.filter(pk__in=(self.from_task_id, self.to_task_id))
            .values_list("pk", "mission__campaign_id")
        )
        if len(campaigns) == 2 and len(set(campaigns.values())) != 1:
            raise ValidationError(_("Tasks can only be related within the same campaign."))

        if self.relation_type in BLOCKING_RELATIONS and campaigns:
            self._reject_cycle(next(iter(campaigns.values())))

    def _reject_cycle(self, campaign_id):
        """Refuse an edge that closes an ordering loop.

        Checked across the blocking family as a whole — "A blocks B" plus
        "B precedes A" is as circular as either type alone. One query, then a
        walk bounded to the campaign. Without it the blocker badge would read
        "blocked" forever on every task in the ring, and people stop reading a
        warning that is always on.
        """
        edges = defaultdict(set)
        pairs = (
            TaskRelation.objects
            .filter(
                relation_type__in=BLOCKING_RELATIONS,
                from_task__mission__campaign_id=campaign_id,
            )
            .exclude(pk=self.pk)
            .values_list("from_task_id", "to_task_id")
        )
        for source, target in pairs:
            edges[source].add(target)
        edges[self.from_task_id].add(self.to_task_id)

        seen, stack = set(), [self.to_task_id]
        while stack:
            node = stack.pop()
            if node == self.from_task_id:
                raise ValidationError(_("This would create a circular blocking chain."))
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges[node])

    def save(self, *args, **kwargs):
        self._canonicalize()
        if self._state.adding:
            # Strict on insert only. SyncService restores rows with
            # update_or_create, and a historical cross-campaign edge must not
            # abort a whole restore.
            self.full_clean()
        super().save(*args, **kwargs)



class MissionAttachment(DomainEntity):
    """A vault file pinned to a mission.

    The link is data; the bytes stay vault-governed, so downloads go through
    vault's own access rules and removing the link never deletes the file.

    Restore caveat, accepted per the TranscriptArtifact precedent: no host
    syncs the vault app, so a cross-instance restore can carry attachments
    whose vault_file no longer resolves.
    """

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="attachments")
    vault_file = models.ForeignKey(
        "vault.VaultFile",
        # CASCADE, not PROTECT: an attachment is a pointer, not provenance,
        # and PROTECT would make vault deletions fail with an error vault's
        # own UI cannot explain.
        on_delete=models.CASCADE,
        related_name="kanban_attachments",
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Display name override; falls back to the file's title.",
    )
    added_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "vault_file"],
                name="unique_mission_attachment",
            ),
        ]

    def __str__(self):
        return self.label or self.vault_file.title

    @property
    def display_name(self):
        return self.label or self.vault_file.title


class DocumentationPage(AbstractPage):
    """A wiki page. The project is the space; pages form a tree inside it.

    This was one page per mission, one-to-one, editable only in Django admin.
    That shape could hold a mission's overview and nothing else — no index, no
    "how we deploy", no page about two missions at once — and it put the only
    writing surface behind a mission detail page. cyprian's own README named the
    captivity as its founding motivation.

    Now: a page belongs to a PROJECT, which is the space, and to an optional
    parent, which makes the tree. It MAY still name a mission — that is what the
    migrated rows keep, and what the mission page lists.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="wiki_pages",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    mission = models.ForeignKey(
        Mission,
        # SET_NULL, not CASCADE: deleting a mission must not delete the prose
        # written about it. The page outlives what it documents, which is the
        # ordinary case for a wiki and was impossible under the one-to-one.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wiki_pages",
        help_text="Optional: the mission this page is about.",
    )
    is_manual = models.BooleanField(
        default=False,
        help_text="If true, this page is an instruction / how-to manual.",
    )
    order = models.PositiveIntegerField(default=0)

    # Redeclared to drop AbstractPage's `unique=True`. Overriding a field
    # inherited from an ABSTRACT base is allowed — the prohibition is on
    # concrete inheritance — and a global slug namespace is simply wrong for a
    # wiki, where two projects both want a page called "getting-started". The
    # constraint below scopes it; save() resolves collisions within one project.
    slug = models.SlugField(blank=True)

    # THE read model. Every host renders this and nothing else: studio and
    # aurelian install kanban from the shared wheel and have no cyprian to parse
    # a vault file with, and five of six zenobia profiles historically had none
    # either. Written on every save through the bridge, already sanitised —
    # Document.from_dict runs sanitize_content on the way in, which is what makes
    # rendering it with |safe legitimate rather than hopeful.
    body_html = models.TextField(blank=True)

    # The cyprian document this page's prose is edited in. Nullable because a
    # page exists before anyone opens the writer, and on hosts without cyprian
    # nothing ever mints one.
    #
    # editable=False is load-bearing and not cosmetic: this column is the trust
    # anchor the cyprian bridge authorises against, so it must be writable ONLY
    # by cyprian.bridge.open_document. Put it on a ModelForm, in admin fields,
    # or in an API serialiser, and a project member can point a page at any
    # VaultFile pk on the instance and have the bridge hand them somebody else's
    # private document. See cyprian/bridge.py.
    vault_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="kanban_wiki_pages",
    )

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "Documentation Page"
        verbose_name_plural = "Documentation Pages"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"],
                name="kanban_docpage_slug_per_project",
            ),
        ]

    def save(self, *args, **kwargs):
        # AbstractPage.save slugifies a blank slug but knows nothing about the
        # scope, so two pages titled "Overview" in one project would collide on
        # the constraint instead of getting distinct slugs.
        if not self.slug:
            self.slug = slugify(self.title)
        base, n = self.slug or "page", 1
        clash = DocumentationPage.objects.filter(
            project_id=self.project_id, slug=self.slug)
        if self.pk:
            clash = clash.exclude(pk=self.pk)
        while clash.exists():
            n += 1
            self.slug = f"{base}-{n}"
            clash = DocumentationPage.objects.filter(
                project_id=self.project_id, slug=self.slug)
            if self.pk:
                clash = clash.exclude(pk=self.pk)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A page cannot be its own parent."})
            if self.parent.project_id != self.project_id:
                raise ValidationError(
                    {"parent": "The parent page belongs to a different project."})
            # Bounded walk: a cycle here would hang every tree render, and the
            # tree is drawn on the space home, the page itself and the sidebar.
            seen, node = {self.pk}, self.parent
            while node is not None:
                if node.pk in seen:
                    raise ValidationError({"parent": "That would make a loop."})
                seen.add(node.pk)
                node = node.parent
        if self.mission_id and self.mission.campaign.project_id != self.project_id:
            raise ValidationError(
                {"mission": "That mission belongs to a different project."})

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("kanban:wiki_page", args=[self.project_id, self.slug])

    def ancestors(self):
        """Root-first, for breadcrumbs. Bounded by the same depth cap as the tree."""
        chain, node, guard = [], self.parent, 0
        while node is not None and guard < WIKI_MAX_DEPTH:
            chain.append(node)
            node = node.parent
            guard += 1
        return list(reversed(chain))


#: The `document.meta` key a page's cyprian document is stamped with, so a file
#: that is downloaded and restored still says what it belongs to. A breadcrumb
#: only — the bridge authorises on `vault_file`, never on this. Defined here
#: rather than in the plugin so that importing it costs no plugin registration.
KANBAN_PAGE_META = "kanban_page"

# How deep the tree is walked, for breadcrumbs and for the recursive template
# include. Not a limit on what can be created — clean() already refuses loops —
# but a floor under the cost of drawing a tree somebody nested absurdly.
WIKI_MAX_DEPTH = 12

