from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0015_remove_vaultinvoice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BucketCopyLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_count", models.PositiveIntegerField(default=1)),
                ("performed_at", models.DateTimeField(auto_now_add=True)),
                ("from_bucket", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="copies_out", to="vault.bucket")),
                ("to_bucket", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="copies_in", to="vault.bucket")),
                ("performed_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Bucket Copy Log", "verbose_name_plural": "Bucket Copy Logs", "ordering": ["-performed_at"]},
        ),
    ]
