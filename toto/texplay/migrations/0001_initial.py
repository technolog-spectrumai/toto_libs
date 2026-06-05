from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("vault", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TexPlayJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"),
                        ("running", "Running"),
                        ("success", "Success"),
                        ("failed",  "Failed"),
                    ],
                    default="pending",
                    max_length=20,
                )),
                ("log", models.TextField(blank=True)),
                ("celery_task_id", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("vault_file", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="texplay_jobs",
                    to="vault.vaultfile",
                )),
                ("pdf_vault_file", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="texplay_source_jobs",
                    to="vault.vaultfile",
                )),
            ],
            options={
                "verbose_name": "TeX Play Job",
                "verbose_name_plural": "TeX Play Jobs",
                "ordering": ["-created_at"],
            },
        ),
    ]
