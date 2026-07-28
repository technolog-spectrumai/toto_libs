"""Drop the central tables — quota's models are abstract from here on.

Each metered app now carries its own policy and usage tables. Nothing read the
rows this deletes: no policy was ever seeded (so the one live check_quota call
always passed) and the usage events were write-only telemetry with no consumer.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("quota", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="QuotaPolicy"),
        migrations.DeleteModel(name="UsageEvent"),
    ]
