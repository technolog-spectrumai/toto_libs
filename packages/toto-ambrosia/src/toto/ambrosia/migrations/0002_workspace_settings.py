# Hand-written: the split's migrations were reset in 2026-08 and the gate runs
# `makemigrations --check --dry-run`, so a new field ships with its migration in
# the same commit or the gate fails.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ambrosia', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='settings',
            # Per-lab overrides, namespaced by the app that owns each key.
            # Empty default: every existing workspace keeps the host defaults.
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
