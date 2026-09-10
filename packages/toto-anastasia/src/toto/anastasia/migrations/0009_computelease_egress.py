"""Per-Capsule opt-in to filtered internet access.

ADDITIVE AND DEFAULT FALSE. Every Capsule reserved before this migration keeps
exactly the posture it had — no network at all — because a default of True
would hand the internet to capacity whose owner never asked for it and whose
owner is not around to be asked.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("anastasia", "0008_capsulesample")]

    operations = [
        migrations.AddField(
            model_name="computelease",
            name="egress",
            field=models.BooleanField(
                default=False,
                help_text="Let jobs in this Capsule reach the internet, "
                          "through this platform's filtering proxy. Only the "
                          "destinations your administrator allows are "
                          "reachable."),
        ),
    ]
