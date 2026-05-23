import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("magistrate", "0001_initial"),
        ("people", "0003_person_is_federal_agent"),
        ("socialhub", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MagistrateDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_type", models.CharField(
                    choices=[
                        ("mobilization_call", "Mobilization Call"),
                        ("emergency_declare", "Emergency Declaration"),
                        ("tribunal_order", "Tribunal Order"),
                        ("trade_order", "Trade Order"),
                        ("finance_directive", "Finance Directive"),
                        ("public_order_directive", "Public Order Directive"),
                        ("legislation_fast_track", "Legislation Fast-Track"),
                        ("general", "General Directive"),
                    ],
                    db_index=True, max_length=30,
                )),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(
                    choices=[("active", "Active"), ("revoked", "Revoked"), ("reviewed", "Reviewed by Assembly")],
                    db_index=True, default="active", max_length=20,
                )),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("magistrate", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="decisions", to="magistrate.magistrate",
                )),
                ("community", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="magistrate_decisions", to="socialhub.community",
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="reviewed_magistrate_decisions", to="people.person",
                )),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Magistrate decision", "verbose_name_plural": "Magistrate decisions"},
        ),
        migrations.AddIndex(
            model_name="magistratedecision",
            index=models.Index(fields=["community", "status"], name="magistrate__dec_comm_idx"),
        ),
    ]
