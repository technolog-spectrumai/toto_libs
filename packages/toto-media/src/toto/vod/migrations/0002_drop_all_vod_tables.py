from django.db import migrations


class Migration(migrations.Migration):
    """Drop all VOD-specific models; VOD app is now a thin vault play-plugin host."""

    dependencies = [
        ("vod", "0001_initial"),
    ]

    operations = [
        # Events and grants reference VodVideo/VodCollection — delete first.
        migrations.DeleteModel("VodPlaybackEvent"),
        migrations.DeleteModel("VodAccessGrant"),
        migrations.DeleteModel("VodVideo"),
        migrations.DeleteModel("VodCollection"),
    ]
