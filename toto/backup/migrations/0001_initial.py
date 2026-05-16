from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        ('gervazy', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BackupProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('verify_key', models.TextField(blank=True, help_text='Public key PEM used to verify incoming backup signatures.', null=True)),
                ('platform', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='backup_profile', to='core.platform')),
                ('signing_key', models.ForeignKey(blank=True, help_text='Private key used to sign outbound backup packages.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='backup_profiles', to='gervazy.encryptedprivatekey')),
            ],
        ),
    ]
