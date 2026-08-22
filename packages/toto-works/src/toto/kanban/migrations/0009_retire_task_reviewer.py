"""Turn the single-reviewer gate into consensus rows.

``Task.reviewer`` named one person who alone could complete a task. That gate
is gone from ``promote_task`` and from the JSON API (see
``work.done_blocked_reason``); this migration carries what it MEANT across, so
the boards that used it keep a review step instead of silently losing one.

Three things happen:

1. The canonical ``ConsensusPolicy`` rows are seeded — 1 of 1, 2 of 3, 3 of 5.
   Only the first is marked default.
2. Every mission holding at least one reviewered task gets the 1-of-1 policy,
   which is the honest translation of "one person signs this off".
3. Every reviewered task gets a ``TaskReviewer`` row, so the person who held
   the gate is still the person eligible to review.

**This changes behaviour on the boards it touches**, deliberately and per
decision 4: such a task now needs an accepted Submission to reach DONE, where
before it needed a click from one named user. Missions that never named a
reviewer are not touched and keep no gate at all.

``Task.reviewer`` itself is left in place for one release. Nothing reads it as
a gate any more; dropping the column belongs in its own migration, so that a
bisect can separate "the behaviour changed" from "the schema changed".
"""

from django.db import migrations

#: (name, required_reviews, required_accepts, reject_threshold, is_default)
CANONICAL = (
    ("1 of 1", 1, 1, 1, True),
    ("2 of 3", 3, 2, 2, False),
    ("3 of 5", 5, 3, 3, False),
)


def forwards(apps, schema_editor):
    ConsensusPolicy = apps.get_model("kanban", "ConsensusPolicy")
    TaskReviewer = apps.get_model("kanban", "TaskReviewer")
    Task = apps.get_model("kanban", "Task")
    Mission = apps.get_model("kanban", "Mission")

    policies = {}
    for name, reviews, accepts, rejects, is_default in CANONICAL:
        policies[name], _created = ConsensusPolicy.objects.get_or_create(
            name=name,
            defaults={
                "required_reviews": reviews,
                "required_accepts": accepts,
                "reject_threshold": rejects,
                "is_default": is_default,
            },
        )
    one_of_one = policies["1 of 1"]

    reviewered = (Task.objects
                  .filter(reviewer__isnull=False)
                  .select_related("reviewer"))

    mission_ids = set()
    rows = []
    for task in reviewered.iterator():
        person_id = task.reviewer.person_id
        if person_id is None:
            continue
        mission_ids.add(task.mission_id)
        rows.append(TaskReviewer(task_id=task.pk, person_id=person_id))

    # ignore_conflicts: this migration must be safe to re-run against a
    # database where somebody has already added a roster row by hand.
    TaskReviewer.objects.bulk_create(rows, ignore_conflicts=True)

    # Only missions that named no policy of their own — an explicit choice
    # already made is not ours to overwrite.
    Mission.objects.filter(
        pk__in=mission_ids, consensus_policy__isnull=True
    ).update(consensus_policy=one_of_one)


def backwards(apps, schema_editor):
    """Undo the wiring, keep the vocabulary.

    The policy ROWS stay: they are reference data an operator may have pointed
    other missions at, and deleting them on a rollback would take those
    missions' rules with them.
    """
    ConsensusPolicy = apps.get_model("kanban", "ConsensusPolicy")
    TaskReviewer = apps.get_model("kanban", "TaskReviewer")
    Mission = apps.get_model("kanban", "Mission")

    one_of_one = ConsensusPolicy.objects.filter(name="1 of 1").first()
    if one_of_one is not None:
        Mission.objects.filter(consensus_policy=one_of_one).update(
            consensus_policy=None)
    TaskReviewer.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0008_work_engine"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
