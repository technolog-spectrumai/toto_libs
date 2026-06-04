import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mobilization", "0003_emergencystatus_allows_asset_requisition_and_more"),
        ("incidents", "0001_initial"),
    ]

    operations = [
        # Clear stale evidence rows that referenced detections (now orphaned)
        migrations.RunPython(
            lambda apps, schema_editor: apps.get_model("mobilization", "MobilizationReportEvidence").objects.all().delete(),
            migrations.RunPython.noop,
        ),
        # Remove old unique_together on (report, detection)
        migrations.AlterUniqueTogether(
            name="mobilizationreportevidence",
            unique_together=set(),
        ),
        # Remove detection FK
        migrations.RemoveField(
            model_name="mobilizationreportevidence",
            name="detection",
        ),
        # Add incident FK (nullable — evidence can exist without an incident if needed)
        migrations.AddField(
            model_name="mobilizationreportevidence",
            name="incident",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="mobilization_evidence",
                to="incidents.incident",
                null=True,
                blank=True,
            ),
        ),
        # Restore unique_together on (report, incident)
        migrations.AlterUniqueTogether(
            name="mobilizationreportevidence",
            unique_together={("report", "incident")},
        ),
    ]
