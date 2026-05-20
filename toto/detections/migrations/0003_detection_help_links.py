from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("detections", "0002_bountypayment_receiver_ledger_account"),
        ("kanban", "0003_task_reviewer"),
    ]

    operations = [
        migrations.AddField(
            model_name="detection",
            name="mitigation_task",
            field=models.ForeignKey(
                blank=True,
                help_text="Kanban task that mitigates or resolves this detection.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="detection_mitigations",
                to="kanban.task",
            ),
        ),
    ]
