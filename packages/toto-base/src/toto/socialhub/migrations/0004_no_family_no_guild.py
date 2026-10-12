"""No "Family" and no "Guild" among the kinds of community (2026-10-12): a
community that was either becomes Other, and the two choices leave the
column's list. "Educational" joins it."""

from django.db import migrations, models


def to_other(apps, schema_editor):
    (apps.get_model("socialhub", "Community").objects
     .filter(org_type__in=("family", "guild")).update(org_type="other"))


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0003_invitation"),
    ]

    operations = [
        migrations.RunPython(to_other, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="community",
            name="org_type",
            field=models.CharField(
                choices=[("company", "Company"), ("non_profit", "Non-Profit"),
                         ("educational", "Educational"), ("other", "Other")],
                default="other", help_text="Type of organization", max_length=20),
        ),
    ]
