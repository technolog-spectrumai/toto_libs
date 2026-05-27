from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vault', '0015_remove_vaultinvoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='bucket',
            name='storage_backend',
            field=models.CharField(
                choices=[('local', 'Local'), ('s3', 'S3-compatible')],
                default='local',
                help_text='Storage backend for files in this bucket.',
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='bucket',
            name='storage_config',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    'Non-secret backend config: bucket_name, endpoint_url, region_name, '
                    'prefix, use_ssl, addressing_style, aws_profile. '
                    'Credentials must come from environment variables, not this field.'
                ),
            ),
        ),
    ]
