from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assembly", "0003_emergency_status_source_proposal"),
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
                ],
                max_length=20,
            ),
        ),
    ]
