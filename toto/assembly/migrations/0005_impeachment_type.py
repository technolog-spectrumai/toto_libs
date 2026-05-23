from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assembly", "0004_magistrate_election_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="assemblyproposal",
            name="proposal_type",
            field=models.CharField(
                choices=[
                    ("rule", "Rule"),
                    ("asset_tax", "Asset Transaction Tax"),
                    ("emg_declare", "Emergency Declaration"),
                    ("mag_elect", "Magistrate Election"),
                    ("impeach", "Impeachment"),
                ],
                max_length=20,
            ),
        ),
    ]
