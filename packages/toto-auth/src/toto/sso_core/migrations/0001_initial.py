# sso_core's first migration — the RecoveryTicket table. The app existed for
# years with no models, so live databases pick this up as one new table and
# nothing else.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecoveryTicket",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("approver_rule", models.CharField(choices=[
                    ("patron", "Patron"), ("referrer", "Membership referrer"),
                    ("staff", "Staff queue")], max_length=12)),
                ("status", models.CharField(choices=[
                    ("pending", "Pending"), ("approved", "Approved"),
                    ("rejected", "Rejected"), ("used", "Used"),
                    ("expired", "Expired")], default="pending", max_length=12)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("request_expires_at", models.DateTimeField()),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("link_sha256", models.CharField(blank=True, db_index=True,
                                                 max_length=64)),
                ("link_expires_at", models.DateTimeField(blank=True, null=True)),
                ("approver", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="recovery_tickets_to_approve",
                    to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="recovery_tickets",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at"]},
        ),
        migrations.AddConstraint(
            model_name="recoveryticket",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("user",),
                name="one_pending_recovery_per_user",
            ),
        ),
    ]
