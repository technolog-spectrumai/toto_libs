from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("vault", "0001_initial"),
        ("workflows", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FileServiceRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("service_key", models.CharField(max_length=64)),
                ("args", models.TextField(blank=True)),
                ("status", models.CharField(
                    choices=[("pending", "Pending"), ("running", "Running"), ("success", "Success"), ("failed", "Failed")],
                    default="pending", max_length=20,
                )),
                ("stdout", models.TextField(blank=True)),
                ("stderr", models.TextField(blank=True)),
                ("output_file_pks", models.JSONField(blank=True, default=list)),
                ("task_id", models.CharField(blank=True, max_length=255)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("bucket", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="file_service_runs", to="vault.bucket")),
                ("input_file", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="file_service_runs", to="vault.vaultfile")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="file_service_runs", to=settings.AUTH_USER_MODEL)),
                ("workflow_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="file_service_runs", to="workflows.workflowrun")),
            ],
            options={
                "ordering": ["-started_at"],
                "indexes": [
                    models.Index(fields=["bucket", "service_key"], name="fileservice_bucket__4b76d8_idx"),
                    models.Index(fields=["status", "started_at"], name="fileservice_status_58044f_idx"),
                ],
            },
        ),
    ]
