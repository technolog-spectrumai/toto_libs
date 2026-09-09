"""A Gear records which isolation it was mounted under, 2026-09-10.

Blank on every existing row, and that is the correct reading rather than a
gap to backfill: those Gears were mounted by an executor that could not report
a tier, so the honest value is "unknown". The page treats unknown as the
WEAKEST claim — a container beside the vault — because the failure worth
preventing is telling somebody their code ran in a virtual machine when it did
not, and the reverse understates rather than misleads.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anastasia', '0004_drop_warm_pools'),
    ]

    operations = [
        migrations.AddField(
            model_name='gearruntime',
            name='tier',
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
