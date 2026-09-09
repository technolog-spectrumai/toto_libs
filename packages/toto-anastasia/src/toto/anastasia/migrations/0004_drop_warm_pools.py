"""Warm pools leave, 2026-09-10.

A fresh sandbox per job makes them meaningless, and they were already
vestigial: `served_warm` was hardcoded False by the manager, `GearEvent.WARM`
was never written by anything, and the caller never sent a `warm` key over the
wire. What existed was the storage for a feature nothing operated.

A NEW migration rather than an edit to 0001. 0001 is deployed — amending it
would leave every live database describing a schema it does not have.

The GearEvent.kind AlterField carries no data change: it shortens the choices
list so `makemigrations --check` stays quiet. `max_length=12` is still right —
the longest surviving kind is "reconcile", at nine.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anastasia', '0003_computelease_permanent_home'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='computelease',
            name='warm_policy',
        ),
        migrations.RemoveField(
            model_name='execution',
            name='served_warm',
        ),
        migrations.AlterField(
            model_name='gearevent',
            name='kind',
            field=models.CharField(choices=[('reserve', 'Reserved'), ('mount', 'Mounted'), ('unmount', 'Unmounted'), ('release', 'Released'), ('expire', 'Expired'), ('execute', 'Execution'), ('reconcile', 'Reconciled'), ('degrade', 'Degraded')], max_length=12),
        ),
    ]
