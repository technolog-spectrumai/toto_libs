"""Subscription.for_admins: whether the row is on a plan for administrators
(2026-09-28), stored so the admin can list and filter by it.

The model sets the flag from the plan on every save; this marks the rows that
already exist. The ladder is a FILE, not a table, so there is no historical
version of it to read here: the step asks the plan registry — the ladder this
host runs now, which is exactly what `Subscription.save` would write — and,
should the registry not load at migrate time, falls back to the one key every
shipped ladder gives its admin plan, ``superuser``. Rows are only ever marked,
never cleared, so re-running it or running it on a stale row is harmless; the
next save of each row sets the flag from the plan anyway.
"""

from django.db import migrations, models

FALLBACK_ADMIN_KEYS = ("superuser",)


def _admin_plan_keys():
    try:
        from toto.subscriptions import plans

        return tuple(plan.key for plan in plans.all_plans() if plan.admin_only)
    except Exception:  # noqa: BLE001 - a ladder fault must not stop a migrate
        return FALLBACK_ADMIN_KEYS


def mark_admin_rows(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    keys = _admin_plan_keys()
    if keys:
        Subscription.objects.filter(plan_key__in=keys).update(for_admins=True)


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0002_expiry_and_lapse_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="for_admins",
            field=models.BooleanField(
                default=False, editable=False,
                help_text="Set from the plan: only Django superusers may hold a plan for administrators.",
                verbose_name="for admins"),
        ),
        migrations.RunPython(mark_admin_rows, migrations.RunPython.noop),
    ]
