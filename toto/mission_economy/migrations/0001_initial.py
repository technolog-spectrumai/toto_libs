from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assets", "0003_initial"),
        ("kanban", "0002_initial"),
        ("people", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PractitionerIncomeProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "practitioner",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="income_profile",
                        to="kanban.practitioner",
                    ),
                ),
                (
                    "default_income_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="practitioner_income_profiles",
                        to="assets.ledgeraccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "Practitioner Income Profile",
                "verbose_name_plural": "Practitioner Income Profiles",
            },
        ),
        migrations.CreateModel(
            name="PractitionerAllowance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "practitioner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allowances",
                        to="kanban.practitioner",
                    ),
                ),
                (
                    "payer_account",
                    models.ForeignKey(
                        help_text="Account that pays this allowance. Defaults to the project owner's wallet.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mission_economy_allowances_to_pay",
                        to="assets.ledgeraccount",
                    ),
                ),
                (
                    "recipient_account",
                    models.ForeignKey(
                        blank=True,
                        help_text="Account that receives the allowance. Resolved from practitioner if blank.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mission_economy_allowances_to_receive",
                        to="assets.ledgeraccount",
                    ),
                ),
                (
                    "asset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mission_economy_practitioner_allowances",
                        to="assets.asset",
                    ),
                ),
                ("amount_base_units", models.BigIntegerField(help_text="Amount in the asset's smallest unit (no decimals, no floats).")),
                (
                    "allowance_type",
                    models.CharField(
                        choices=[
                            ("per_diem", "Per Diem"),
                            ("hourly", "Hourly"),
                            ("fixed", "Fixed"),
                            ("travel", "Travel"),
                            ("meal", "Meal"),
                            ("other", "Other"),
                        ],
                        default="fixed",
                        max_length=20,
                    ),
                ),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_until", models.DateField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, null=True)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="ProjectTokenization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tokenization",
                        to="kanban.project",
                    ),
                ),
                (
                    "asset",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="project_tokenization",
                        to="assets.asset",
                    ),
                ),
                (
                    "supervisor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="supervised_project_tokenizations",
                        to="people.person",
                    ),
                ),
                (
                    "defaulted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="defaulted_project_tokenizations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("defaulted", "Defaulted")],
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "default_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("insolvency", "Insolvency"),
                            ("abandonment", "Abandonment"),
                            ("fraud", "Fraud"),
                            ("technical", "Technical Failure"),
                            ("other", "Other"),
                        ],
                        max_length=40,
                    ),
                ),
                ("default_note", models.TextField(blank=True)),
                ("defaulted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["status"], name="meco_proj_status_idx"),
                    models.Index(fields=["created_at"], name="meco_proj_created_idx"),
                ],
            },
        ),
    ]
