import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="QuotaPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("app_label", models.CharField(db_index=True, max_length=100)),
                ("metric_code", models.CharField(db_index=True, max_length=100)),
                ("subject_type", models.CharField(blank=True, db_index=True, max_length=100)),
                ("subject_id", models.CharField(blank=True, db_index=True, max_length=255)),
                ("limit", models.DecimalField(decimal_places=10, max_digits=30)),
                ("unit", models.CharField(blank=True, max_length=100)),
                ("period", models.CharField(
                    choices=[("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("yearly", "Yearly"), ("lifetime", "Lifetime")],
                    default="daily",
                    max_length=20,
                )),
                ("mode", models.CharField(
                    choices=[("track", "Track only"), ("warn", "Warn"), ("block", "Block")],
                    default="block",
                    max_length=10,
                )),
                ("active", models.BooleanField(default=True)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Quota Policy",
                "verbose_name_plural": "Quota Policies",
                "ordering": ["app_label", "metric_code"],
            },
        ),
        migrations.CreateModel(
            name="UsageEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("app_label", models.CharField(max_length=100)),
                ("metric_code", models.CharField(max_length=100)),
                ("quantity", models.DecimalField(decimal_places=10, max_digits=30)),
                ("unit", models.CharField(blank=True, max_length=100)),
                ("subject_type", models.CharField(max_length=100)),
                ("subject_id", models.CharField(max_length=255)),
                ("subject_label", models.CharField(blank=True, max_length=255)),
                ("source_type", models.CharField(blank=True, max_length=100)),
                ("source_id", models.CharField(blank=True, max_length=255)),
                ("source_label", models.CharField(blank=True, max_length=255)),
                ("idempotency_key", models.CharField(blank=True, db_index=True, max_length=512)),
                ("status", models.CharField(
                    choices=[("recorded", "Recorded"), ("voided", "Voided")],
                    default="recorded",
                    max_length=20,
                )),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "verbose_name": "Usage Event",
                "verbose_name_plural": "Usage Events",
                "ordering": ["-occurred_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="quotapolicy",
            constraint=models.UniqueConstraint(
                fields=["app_label", "metric_code", "subject_type", "subject_id"],
                name="quota_unique_policy_per_subject_metric",
            ),
        ),
        migrations.AddIndex(
            model_name="usageevent",
            index=models.Index(
                fields=["app_label", "metric_code", "occurred_at"],
                name="quota_event_app_metric_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="usageevent",
            index=models.Index(
                fields=["subject_type", "subject_id", "metric_code"],
                name="quota_event_subject_metric_idx",
            ),
        ),
    ]
