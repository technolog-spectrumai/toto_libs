from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vault', '0018_alter_vaultfile_file_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='StorageProvider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.SlugField(max_length=64, unique=True)),
                ('display_name', models.CharField(max_length=128)),
                ('endpoint_url_template', models.CharField(
                    blank=True,
                    max_length=256,
                    help_text=(
                        'Endpoint URL, optionally with {region} or {account_id} placeholders. '
                        'Leave blank for AWS default routing.'
                    ),
                )),
                ('default_region', models.CharField(blank=True, max_length=64)),
                ('addressing_style', models.CharField(
                    choices=[('path', 'Path'), ('virtual', 'Virtual'), ('auto', 'Auto')],
                    default='auto',
                    max_length=8,
                )),
                ('use_ssl', models.BooleanField(default=True)),
                ('is_builtin', models.BooleanField(
                    default=True,
                    help_text='Seeded by ingress — safe to re-run ingress to reset.',
                )),
            ],
            options={
                'verbose_name': 'Storage Provider',
                'verbose_name_plural': 'Storage Providers',
                'ordering': ['display_name'],
            },
        ),
        migrations.AddField(
            model_name='bucket',
            name='provider',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='buckets',
                to='vault.storageprovider',
                help_text='Provider preset used when storage_backend is S3-compatible.',
            ),
        ),
        migrations.AddField(
            model_name='bucket',
            name='public_base_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text=(
                    'Optional CDN or public base URL (e.g. https://cdn.example.com/vault/). '
                    'When set, get_public_file_url() returns a direct link per file.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='bucket',
            name='storage_backend',
            field=models.CharField(
                choices=[('local', 'Local'), ('s3', 'S3-compatible'), ('remote_toto', 'Remote Toto Server')],
                default='local',
                help_text='Storage backend for files in this bucket.',
                max_length=16,
            ),
        ),
    ]
