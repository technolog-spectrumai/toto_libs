# Generated manually on 2026-05-09

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0002_zone"),
    ]

    operations = [
        migrations.CreateModel(
            name="RouteChain",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddField(
            model_name="route",
            name="route_chain",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="routes",
                to="locations.routechain",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="sequence",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
