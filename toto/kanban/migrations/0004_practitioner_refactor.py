"""
Kanban Practitioner refactor.

Schema changes:
  - Add Practitioner model
  - Add PractitionerAllowance model
  - Task.assignee: Person FK  →  Practitioner FK  (data preserved)
  - Task.reviewer: Person FK  →  Practitioner FK  (data preserved)
  - Column.auditors: User M2M  →  Practitioner M2M
  - Project.collaborators M2M  →  removed
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


# ---------------------------------------------------------------------------
# Data migration helpers
# ---------------------------------------------------------------------------

def _migrate_task_participants(apps, schema_editor):
    Task = apps.get_model("kanban", "Task")
    Practitioner = apps.get_model("kanban", "Practitioner")

    for task in Task.objects.select_related(
        "legacy_assignee",
        "legacy_reviewer",
        "mission__campaign__project",
    ).iterator():
        if not task.mission_id:
            continue
        try:
            project = task.mission.campaign.project
        except Exception:
            continue

        changed = False

        if task.legacy_assignee_id:
            p, _ = Practitioner.objects.get_or_create(
                project=project,
                person=task.legacy_assignee,
                defaults={"role": "contributor", "is_active": True, "uid": uuid.uuid4()},
            )
            task.assignee = p
            changed = True

        if task.legacy_reviewer_id:
            p, _ = Practitioner.objects.get_or_create(
                project=project,
                person=task.legacy_reviewer,
                defaults={"role": "reviewer", "is_active": True, "uid": uuid.uuid4()},
            )
            task.reviewer = p
            changed = True

        if changed:
            task.save(update_fields=["assignee", "reviewer"])


def _noop(apps, schema_editor):
    pass


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0003_task_reviewer"),
        ("people", "0001_initial"),
        ("assets", "0001_initial"),
    ]

    operations = [
        # ── 1. Add Practitioner ────────────────────────────────────────────
        migrations.CreateModel(
            name="Practitioner",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("role", models.CharField(
                    choices=[
                        ("contributor", "Contributor"),
                        ("reviewer", "Reviewer"),
                        ("auditor", "Auditor"),
                        ("manager", "Manager"),
                        ("observer", "Observer"),
                    ],
                    default="contributor",
                    max_length=20,
                )),
                ("is_active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, null=True)),
                ("project", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="practitioners",
                    to="kanban.project",
                )),
                ("person", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="project_practitioner_roles",
                    to="people.person",
                )),
            ],
            options={"abstract": False},
        ),
        migrations.AddConstraint(
            model_name="practitioner",
            constraint=models.UniqueConstraint(fields=["project", "person"], name="unique_practitioner_per_project"),
        ),

        # ── 2. Rename old Task FK columns before replacing them ───────────
        migrations.RenameField(model_name="task", old_name="assignee", new_name="legacy_assignee"),
        migrations.RenameField(model_name="task", old_name="reviewer", new_name="legacy_reviewer"),

        # ── 3. Add new Practitioner FK columns (nullable for data step) ───
        migrations.AddField(
            model_name="task",
            name="assignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_tasks",
                to="kanban.practitioner",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="reviewer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="review_tasks",
                help_text="Optional reviewer who signs off the task before it is completed.",
                to="kanban.practitioner",
            ),
        ),

        # ── 4. Replace Column.auditors (User M2M → Practitioner M2M) ─────
        migrations.RemoveField(model_name="column", name="auditors"),
        migrations.AddField(
            model_name="column",
            name="auditors",
            field=models.ManyToManyField(
                blank=True,
                related_name="audited_columns",
                to="kanban.practitioner",
                help_text="Practitioners who can move tasks into this column.",
            ),
        ),

        # ── 5. Remove Project.collaborators ───────────────────────────────
        migrations.RemoveField(model_name="project", name="collaborators"),

        # ── 6. Data migration: create Practitioners from old Person FKs ───
        migrations.RunPython(_migrate_task_participants, _noop),

        # ── 7. Drop legacy columns ────────────────────────────────────────
        migrations.RemoveField(model_name="task", name="legacy_assignee"),
        migrations.RemoveField(model_name="task", name="legacy_reviewer"),

        # ── 8. Add PractitionerAllowance ──────────────────────────────────
        migrations.CreateModel(
            name="PractitionerAllowance",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("amount_base_units", models.BigIntegerField(
                    help_text="Amount in the asset's smallest unit (no decimals, no floats).",
                )),
                ("allowance_type", models.CharField(
                    choices=[
                        ("per_diem", "Per Diem"),
                        ("hourly", "Hourly"),
                        ("fixed", "Fixed"),
                        ("travel", "Travel"),
                        ("meal", "Meal"),
                        ("other", "Other"),
                    ],
                    default="fixed",
                    max_length=20,
                )),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_until", models.DateField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, null=True)),
                ("practitioner", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="allowances",
                    to="kanban.practitioner",
                )),
                ("payer_account", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="kanban_allowances_to_pay",
                    to="assets.ledgeraccount",
                    help_text="Account that pays this allowance. Defaults to the project owner's wallet.",
                )),
                ("recipient_account", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="kanban_allowances_to_receive",
                    to="assets.ledgeraccount",
                    help_text="Account that receives the allowance.",
                )),
                ("asset", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="kanban_practitioner_allowances",
                    to="assets.asset",
                )),
            ],
            options={"abstract": False},
        ),
    ]
