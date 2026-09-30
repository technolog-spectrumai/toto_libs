# Buckets administered from Storage → Management (2026-09-30).
#
# - Bucket.owner becomes SET_NULL: deleting an account no longer deletes the
#   buckets it owned (only the Management purge deletes a bucket).
# - Bucket.created_by / created_at: who made it and when (older rows: null).
# - Bucket.last_probe_* and deletion_*: the connection test and the purge state.
# - BucketSecret: an S3 credential sealed under FIELD_ENCRYPTION_KEY.
# - VaultFile.bucket becomes PROTECT: a bucket with files cannot be deleted
#   except by the purge, which takes every file first — nothing is left with
#   bucket=None (which would drop it out of its bucket's clearance keeping).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('vault', '0027_bucket_clearances'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bucket',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='bucket',
            name='created_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='buckets_created', to=settings.AUTH_USER_MODEL),
        ),
        # Added PLAIN first, then made auto_now_add: an AddField with
        # auto_now_add fills every existing row with the migration's own time
        # (the schema editor's effective default for such a field is now()),
        # and every older bucket would show the deploy date as its creation.
        # This way they keep NULL — "not recorded" — as the header says.
        migrations.AddField(
            model_name='bucket',
            name='created_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name='bucket',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='bucket',
            name='last_probe_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='bucket',
            name='last_probe_error',
            field=models.TextField(blank=True, default='', editable=False),
        ),
        migrations.AddField(
            model_name='bucket',
            name='deletion_requested_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='bucket',
            name='deletion_error',
            field=models.TextField(blank=True, default='', editable=False),
        ),
        migrations.AlterField(
            model_name='vaultfile',
            name='bucket',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='files', to='vault.bucket'),
        ),
        migrations.CreateModel(
            name='BucketSecret',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ciphertext', models.BinaryField(editable=False)),
                ('hint', models.CharField(blank=True, max_length=16)),
                ('sealed_at', models.DateTimeField(auto_now=True)),
                ('bucket', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='sealed_secret', to='vault.bucket')),
            ],
            options={
                'verbose_name': 'Bucket secret',
                'verbose_name_plural': 'Bucket secrets',
            },
        ),
    ]
