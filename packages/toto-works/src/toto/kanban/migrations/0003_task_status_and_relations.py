"""Collapse the Column table into a three-value status on Task, and add typed
relations between tasks.

Irreversible on purpose — see raise_irreversible. Snapshot the database before
running this; that snapshot is the only rollback.
"""

import re
import uuid
from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


TODO, IN_PROGRESS, DONE = "todo", "in_progress", "done"


def _norm(name):
    """Fold a column name to a comparison key: 'To-Do' and 'TODO' are one name."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


#: Names seen in the wild, plus the obvious variants someone types into admin.
#: Review sits in the in-progress family: those tasks have no completion
#: timestamp today and every metric counts them as open, so calling them done
#: would retroactively finish unfinished work and inflate every chart.
_BY_NAME = {
    "done": DONE, "completed": DONE, "complete": DONE, "closed": DONE,
    "finished": DONE, "shipped": DONE, "resolved": DONE, "archived": DONE,
    "released": DONE, "accepted": DONE,

    "inprogress": IN_PROGRESS, "progress": IN_PROGRESS, "doing": IN_PROGRESS,
    "wip": IN_PROGRESS, "started": IN_PROGRESS, "active": IN_PROGRESS,
    "review": IN_PROGRESS, "inreview": IN_PROGRESS, "codereview": IN_PROGRESS,
    "peerreview": IN_PROGRESS, "testing": IN_PROGRESS, "qa": IN_PROGRESS,
    "verification": IN_PROGRESS, "verify": IN_PROGRESS,
    "blocked": IN_PROGRESS, "onhold": IN_PROGRESS, "waiting": IN_PROGRESS,

    "todo": TODO, "backlog": TODO, "new": TODO, "open": TODO, "planned": TODO,
    "ready": TODO, "next": TODO, "icebox": TODO, "triage": TODO, "inbox": TODO,
}


def _resolve(name, rank, count):
    """Map one column to a status. Name first, ordinal rank as the fallback.

    Names win over rank because a board whose positions were fat-fingered still
    means what its names say. Rank is 0-based *within the column's own project*,
    which is what makes positions-from-0 and positions-from-1 identical and what
    lets boards of any width map sensibly.
    """
    hit = _BY_NAME.get(_norm(name))
    if hit is not None:
        return hit
    if count <= 1:
        return TODO
    if rank == 0:
        return TODO
    if rank == count - 1:
        return DONE
    return IN_PROGRESS


def map_columns_to_status(apps, schema_editor):
    Column = apps.get_model("kanban", "Column")
    Task = apps.get_model("kanban", "Task")

    by_project = defaultdict(list)
    for column in Column.objects.order_by("project_id", "position", "pk").only(
        "id", "project_id", "position", "name"
    ):
        by_project[column.project_id].append(column)

    status_of_column = {}
    for columns in by_project.values():
        count = len(columns)
        for rank, column in enumerate(columns):
            status_of_column[column.pk] = _resolve(column.name, rank, count)

    # Three statements, not one per task. Nothing guarantees a task's column
    # belongs to its own mission's project (Task.clean was never called by any
    # view, and the API accepted any column id), so rank each column inside its
    # own project rather than assuming the trees agree.
    for status in (TODO, IN_PROGRESS, DONE):
        ids = [pk for pk, value in status_of_column.items() if value == status]
        if ids:
            Task.objects.filter(column_id__in=ids).update(status=status)


def lift_auditors_to_project(apps, schema_editor):
    """Move move-permission from columns to the project.

    Union, not intersection: a practitioner who audited any column audits the
    project. Intersecting would empty the set on any board with differing
    per-column auditors and lock everyone out of their own board.

    This widens privilege — someone who could only move cards into Review can now
    reach Done. That is the cost of collapsing the states, and it is in the
    release notes.
    """
    Column = apps.get_model("kanban", "Column")
    Project = apps.get_model("kanban", "Project")
    Practitioner = apps.get_model("kanban", "Practitioner")
    ProjectCommitment = apps.get_model("kanban", "ProjectCommitment")
    Through = Project.auditors.through

    pairs = set()
    for column in Column.objects.prefetch_related("auditors").only("id", "project_id"):
        for practitioner_id in column.auditors.values_list("id", flat=True):
            pairs.add((column.project_id, practitioner_id))

    # No board may end up with nobody able to move a card — that is a board
    # nobody can use. Fall back to the lead, then to anyone committed, and if
    # the lead has no practitioner seat at all, give them one: they are the
    # lead, and a seat is the only way to hold the grant.
    covered = {project_id for project_id, _ in pairs}
    for project in Project.objects.exclude(pk__in=covered):
        ids = list(
            Practitioner.objects.filter(person_id=project.project_lead_id)
            .values_list("id", flat=True)
        ) or list(
            ProjectCommitment.objects.filter(project=project, is_active=True)
            .values_list("practitioner_id", flat=True)
        )
        if not ids and project.project_lead_id:
            seat = Practitioner.objects.create(
                person_id=project.project_lead_id,
                role="manager",
                is_active=True,
                metadata={"created_by": "kanban.0003 auditor lift"},
            )
            ids = [seat.pk]
        pairs.update((project.pk, practitioner_id) for practitioner_id in ids)

    Through.objects.bulk_create(
        [Through(project_id=p, practitioner_id=a) for p, a in sorted(pairs)],
        ignore_conflicts=True,
    )


def renumber_positions(apps, schema_editor):
    """Make position dense within (mission, status).

    Positions were per-column, so after the collapse two tasks in in_progress
    can both be position 2 and the board order becomes whatever the database
    feels like.
    """
    Task = apps.get_model("kanban", "Task")

    buckets = defaultdict(list)
    for task in Task.objects.order_by("column__position", "position", "pk").only(
        "id", "mission_id", "status", "position"
    ):
        buckets[(task.mission_id, task.status)].append(task)

    changed = []
    for tasks in buckets.values():
        for index, task in enumerate(tasks, start=1):
            if task.position != index:
                task.position = index
                changed.append(task)

    if changed:
        Task.objects.bulk_update(changed, ["position"], batch_size=500)


def reconcile_completed_at(apps, schema_editor):
    """Make completed_at agree with status, in both directions.

    Rows violating the new invariant provably exist: the JSON API's promote and
    demote save only the column, and the detections seeder creates tasks without
    a timestamp. Adding the CheckConstraint on top of them would fail outright —
    and leaving them would make the invariant a lie on day one.

    Backfilling with a blanket now() would dump a cliff of completions on the
    migration date and wreck the current sprint's burndown, so prefer the
    sprint's end where there is one. Both directions leave a breadcrumb in
    metadata, which is what makes an irreversible migration auditable.
    """
    Task = apps.get_model("kanban", "Task")
    now = timezone.now()

    to_fill = list(
        Task.objects.filter(status=DONE, completed_at__isnull=True).select_related("sprint")
    )
    for task in to_fill:
        sprint_end = getattr(task.sprint, "end_time", None)
        task.completed_at = sprint_end if (sprint_end and sprint_end < now) else now
        task.metadata = {**(task.metadata or {}), "completed_at_backfilled": True}
    if to_fill:
        Task.objects.bulk_update(to_fill, ["completed_at", "metadata"], batch_size=500)

    to_clear = list(Task.objects.exclude(status=DONE).filter(completed_at__isnull=False))
    for task in to_clear:
        task.metadata = {
            **(task.metadata or {}),
            "completed_at_cleared": task.completed_at.isoformat(),
        }
        task.completed_at = None
    if to_clear:
        Task.objects.bulk_update(to_clear, ["completed_at", "metadata"], batch_size=500)


def raise_irreversible(apps, schema_editor):
    raise RuntimeError(
        "kanban.0003 cannot be reversed. The collapse is not injective — 'In "
        "Progress' and 'Review' both became 'in_progress' — every Column row and "
        "its uid is gone, and per-column auditor scoping was flattened to a "
        "per-project union. Restore the database snapshot taken before this "
        "migration. Timestamps cleared here are recoverable from "
        "Task.metadata['completed_at_cleared']."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0002_initial"),
    ]

    operations = [
        # NOT NULL with a default from the start: Django backfills every row in
        # one UPDATE and the RunPython below corrects it. A nullable-then-tighten
        # dance costs a second ALTER TABLE and leaves a window where the
        # invariant cannot be enforced.
        migrations.AddField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[("todo", "To do"), ("in_progress", "In progress"), ("done", "Done")],
                db_index=True,
                default="todo",
                max_length=20,
            ),
        ),
        # elidable=False on every data step: a squash that elided them would
        # replay 0001-0003 on an old database and leave every task in todo.
        migrations.RunPython(map_columns_to_status, raise_irreversible, elidable=False),

        # Auditors must be lifted while the columns still exist. Shipping
        # Project.auditors empty would 403 every promote for every user.
        migrations.AddField(
            model_name="project",
            name="auditors",
            field=models.ManyToManyField(
                blank=True,
                help_text="Practitioners who may move tasks between states.",
                related_name="audited_projects",
                to="kanban.practitioner",
            ),
        ),
        migrations.RunPython(lift_auditors_to_project, migrations.RunPython.noop, elidable=False),
        migrations.RunPython(renumber_positions, migrations.RunPython.noop, elidable=False),

        migrations.RemoveField(model_name="column", name="auditors"),
        migrations.RemoveField(model_name="column", name="project"),
        migrations.RemoveField(model_name="task", name="column"),
        migrations.DeleteModel(name="Column"),

        migrations.CreateModel(
            name="TaskRelation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("relation_type", models.CharField(
                    choices=[
                        ("blocks", "blocks"),
                        ("precedes", "precedes"),
                        ("relates", "relates to"),
                        ("duplicates", "duplicates"),
                        ("tests", "tests"),
                        ("implements", "implements"),
                    ],
                    max_length=20,
                )),
                ("note", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="kanban.practitioner",
                )),
                ("from_task", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="outgoing_relations",
                    to="kanban.task",
                )),
                ("to_task", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="incoming_relations",
                    to="kanban.task",
                )),
            ],
            options={"ordering": ("relation_type", "pk")},
        ),
        migrations.AddConstraint(
            model_name="taskrelation",
            constraint=models.UniqueConstraint(
                fields=("from_task", "to_task", "relation_type"),
                name="unique_task_relation",
            ),
        ),
        migrations.AddConstraint(
            model_name="taskrelation",
            constraint=models.CheckConstraint(
                check=models.Q(("from_task", models.F("to_task")), _negated=True),
                name="task_relation_not_self",
            ),
        ),
        migrations.AddIndex(
            model_name="taskrelation",
            index=models.Index(fields=["to_task", "relation_type"], name="taskrel_to_type_idx"),
        ),

        # Must run before AddConstraint or the constraint cannot be added.
        migrations.RunPython(reconcile_completed_at, migrations.RunPython.noop, elidable=False),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["mission", "status"], name="kanban_task_mission_status"),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(completed_at__isnull=False, status="done")
                    | (models.Q(("status", "done"), _negated=True) & models.Q(completed_at__isnull=True))
                ),
                name="kanban_task_completed_at_matches_status",
            ),
        ),

        # Last forwards, so first backwards. Without it the reverse gets as far
        # as re-adding Task.column — a NOT NULL foreign key with no default,
        # onto a populated table — and dies with an IntegrityError that says
        # nothing about why this cannot be undone.
        migrations.RunPython(
            migrations.RunPython.noop, raise_irreversible, elidable=False
        ),
    ]
