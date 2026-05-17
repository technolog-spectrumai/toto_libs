from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("texlab", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompileRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_id", models.CharField(blank=True, max_length=255)),
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
                ("pdf_url", models.CharField(blank=True, max_length=500)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compile_runs",
                        to="texlab.latexworkspace",
                    ),
                ),
                (
                    "latex_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="compile_runs",
                        to="texlab.latexfile",
                    ),
                ),
            ],
            options={"ordering": ["-started_at"]},
        ),
    ]
