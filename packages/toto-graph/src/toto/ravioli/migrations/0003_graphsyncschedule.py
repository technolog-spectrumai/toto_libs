from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ravioli", "0002_rename_verbose_names"),
    ]

    operations = [
        migrations.CreateModel(
            name="GraphSyncSchedule",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("interval_minutes", models.PositiveIntegerField(default=30, help_text="How often to run the full graph sync (minutes).")),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_run_status", models.CharField(
                    choices=[
                        ("idle", "Idle"),
                        ("running", "Running"),
                        ("success", "Success"),
                        ("failed", "Failed"),
                    ],
                    default="idle",
                    max_length=16,
                )),
                ("last_error", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "Graph Sync Schedule",
                "verbose_name_plural": "Graph Sync Schedule",
            },
        ),
    ]
