"""Seed the canonical ConsensusPolicy rows: 1 of 1 (the default), 2 of 3, 3 of 5.

Hand-written on top of the fresh initial migration of the 2026-10-01 reset: the
autodetector never writes data, and nothing else creates these rows, so without
this a fresh database offers an empty policy dropdown. The tuple is the one in
``toto.kanban.work.CANONICAL_POLICIES``, inlined because a migration must not
import app code that may change after it ran.

It is the seeding half of the retired ``0009_retire_task_reviewer``; the other
half converted ``Task.reviewer`` rows, which a fresh database does not have.
The reverse keeps the rows, as 0009's did: they are reference data that
missions may point at.
"""

from django.db import migrations

#: (name, required_reviews, required_accepts, reject_threshold, is_default)
CANONICAL = (
    ("1 of 1", 1, 1, 1, True),
    ("2 of 3", 3, 2, 2, False),
    ("3 of 5", 5, 3, 3, False),
)


def seed(apps, schema_editor):
    ConsensusPolicy = apps.get_model("kanban", "ConsensusPolicy")
    for name, reviews, accepts, rejects, is_default in CANONICAL:
        ConsensusPolicy.objects.get_or_create(
            name=name,
            defaults={
                "required_reviews": reviews,
                "required_accepts": accepts,
                "reject_threshold": rejects,
                "is_default": is_default,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
