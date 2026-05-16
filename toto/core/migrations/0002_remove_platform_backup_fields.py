from django.db import migrations


class Migration(migrations.Migration):
    """Remove backup signing fields from Platform — now live in toto.backup.BackupProfile."""

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(model_name='platform', name='signing_secret'),
        migrations.RemoveField(model_name='platform', name='api_signing_key_out'),
        migrations.RemoveField(model_name='platform', name='api_verify_key_in'),
    ]
