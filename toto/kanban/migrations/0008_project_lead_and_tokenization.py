from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0007_practitioner_decouple_project"),
        ("assets", "0011_tokenization_default_state"),
        ("people", "0003_person_is_federal_agent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name="project",
            old_name="owner",
            new_name="project_lead",
        ),
        migrations.CreateModel(
            name="ProjectTokenization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[("active", "Active"), ("defaulted", "Defaulted")],
                    default="active",
                    max_length=20,
                )),
                ("default_reason", models.CharField(
                    blank=True,
                    choices=[
                        ("no_longer_exists", "Project no longer exists"),
                        ("dissolved", "Project dissolved"),
                        ("other", "Other"),
                    ],
                    max_length=40,
                )),
                ("default_note", models.TextField(blank=True)),
                ("defaulted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("project", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="tokenization",
                    to="kanban.project",
                )),
                ("asset", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="project_tokenization",
                    to="assets.asset",
                )),
                ("supervisor", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="supervised_project_tokenizations",
                    to="people.person",
                )),
                ("defaulted_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="defaulted_project_tokenizations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="projecttokenization",
            index=models.Index(fields=["status"], name="kanban_proj_status_idx"),
        ),
        migrations.AddIndex(
            model_name="projecttokenization",
            index=models.Index(fields=["created_at"], name="kanban_proj_created_idx"),
        ),
    ]
