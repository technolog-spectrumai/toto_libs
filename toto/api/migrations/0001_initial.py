# State-only migration: EmailService table already exists as socialhub_emailservice
# (created by socialhub.0001_initial). No DDL needed — only update ORM state.

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('gervazy', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='EmailService',
                    fields=[
                        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('name', models.CharField(blank=True, help_text='Optional name for this email service. Auto-generated if omitted.', max_length=128, unique=True)),
                        ('email_address', models.EmailField(help_text='SMTP login email address', max_length=254)),
                        ('host', models.CharField(help_text='SMTP server hostname', max_length=255)),
                        ('port', models.PositiveIntegerField(default=587)),
                        ('use_tls', models.BooleanField(default=True)),
                        ('use_ssl', models.BooleanField(default=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('smtp_secret', models.ForeignKey(blank=True, help_text='Gervazy EncryptedSecret holding the SMTP password.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='email_services', to='gervazy.encryptedsecret')),
                    ],
                    options={
                        'db_table': 'socialhub_emailservice',
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
