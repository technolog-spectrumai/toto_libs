from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("texplay", "0001_initial"),
        ("workflows", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="texplayjob",
            name="workflow_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="texplay_jobs",
                to="workflows.workflowrun",
            ),
        ),
    ]
