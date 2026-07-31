"""Drop the Lapis smart-contract layer and Obligation.

The Lapis VM, the ``Contract``/``Agreement`` models (its runtime layer) and the
``Obligation`` model were pulled out of the live app on 2026-07-31 and parked in
``toto_libs/limbo/lapis/`` (see its README). The ledger core stays. This drops the
three now-unused tables. Agreement is deleted first — it has a FK to Contract.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0002_assetsquotapolicy_assetsusageevent_and_more"),
    ]

    operations = [
        migrations.DeleteModel(name="Agreement"),
        migrations.DeleteModel(name="Contract"),
        migrations.DeleteModel(name="Obligation"),
    ]
