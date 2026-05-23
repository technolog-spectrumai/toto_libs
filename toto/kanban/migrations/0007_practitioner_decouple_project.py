from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0006_practitioner_income_account"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="practitioner",
            name="unique_practitioner_per_project",
        ),
        migrations.AlterField(
            model_name="practitioner",
            name="person",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="practitioner_profiles",
                to="people.person",
            ),
        ),
        migrations.RemoveField(
            model_name="practitioner",
            name="project",
        ),
        migrations.CreateModel(
            name="ProjectCommitment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "hours_per_day",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Number of hours per day committed to this project.",
                        max_digits=4,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, null=True)),
                (
                    "practitioner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="commitments",
                        to="kanban.practitioner",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="commitments",
                        to="kanban.project",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddConstraint(
            model_name="projectcommitment",
            constraint=models.UniqueConstraint(
                fields=["practitioner", "project"],
                name="unique_commitment_per_project",
            ),
        ),
    ]
