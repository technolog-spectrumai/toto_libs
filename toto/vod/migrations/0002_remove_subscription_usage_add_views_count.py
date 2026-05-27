from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vod", "0001_initial"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="vodplaybackevent",
            name="vod_vodplay_subscri_72a4a0_idx",
        ),
        migrations.RemoveField(
            model_name="vodplaybackevent",
            name="subscription_usage",
        ),
        migrations.AddField(
            model_name="vodvideo",
            name="views_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
