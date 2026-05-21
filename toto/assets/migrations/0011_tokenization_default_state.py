from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("assets", "0010_move_asset_exchange_request_to_bourse"),
    ]

    operations = [
        migrations.AddField(
            model_name="tokenization",
            name="default_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tokenization",
            name="default_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("no_longer_exists", "Underlying object no longer exists"),
                    ("broken", "Underlying object is broken"),
                    ("lost", "Underlying object is lost"),
                    ("other", "Other"),
                ],
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="tokenization",
            name="defaulted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tokenization",
            name="defaulted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="defaulted_tokenizations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="tokenization",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("defaulted", "Defaulted")],
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="tokenization",
            index=models.Index(fields=["status"], name="assets_toke_status_4058ae_idx"),
        ),
    ]
