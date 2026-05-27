from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vod", "0003_simplify_access_modes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="vodcollection",
            name="readers",
            field=models.ManyToManyField(
                blank=True,
                help_text="Users who can watch private content in this collection.",
                related_name="vod_readable_collections",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="vodcollection",
            name="writers",
            field=models.ManyToManyField(
                blank=True,
                help_text="Users who can upload and manage videos in this collection.",
                related_name="vod_writable_collections",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
