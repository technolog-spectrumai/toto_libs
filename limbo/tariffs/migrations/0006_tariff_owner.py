from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tariffs", "0005_billing_metric"),
    ]

    operations = [
        migrations.AddField(
            model_name="tariff",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                help_text="Only this user can edit the tariff and its items.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_tariffs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
