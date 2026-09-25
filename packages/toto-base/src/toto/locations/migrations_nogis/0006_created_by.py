# Who created an address or a route, so writes can be the creator's or staff's
# (2026-09-25). Additive and nullable: existing rows keep NULL and are
# editable by staff only — nobody is recorded as having made them.
# Identical in migrations/ and migrations_nogis/, as every locations migration is.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("locations", "0005_address_latlon"),
    ]

    operations = [
        migrations.AddField(
            model_name="address",
            name="created_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="route",
            name="created_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL),
        ),
    ]
