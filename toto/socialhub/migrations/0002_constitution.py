from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gervazy', '0001_initial'),
        ('people', '0001_initial'),
        ('socialhub', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Constitution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('slug', models.SlugField(blank=True, max_length=120, unique=True)),
                ('body', models.TextField()),
                ('version', models.CharField(blank=True, help_text='e.g. I, II, 2025-rev1', max_length=50)),
                ('is_active', models.BooleanField(default=True, help_text='Only the active constitution is shown on the community page.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('community', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='constitutions',
                    to='socialhub.community',
                )),
            ],
            options={
                'verbose_name': 'Constitution',
                'verbose_name_plural': 'Constitutions',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ConstitutionSignature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('signed_at', models.DateTimeField(blank=True, null=True)),
                ('signature_data', models.TextField(blank=True, help_text='Base64-encoded PNG — decorative handwritten signature image.')),
                ('signing_payload', models.TextField(blank=True, help_text='Canonical UTF-8 payload that was signed.')),
                ('cryptographic_signature', models.TextField(blank=True, help_text='Base64-encoded Ed25519 signature over signing_payload.')),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('constitution', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='signatures',
                    to='socialhub.constitution',
                )),
                ('person', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='constitution_signatures',
                    to='people.person',
                )),
                ('signing_key', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='constitution_signatures',
                    to='gervazy.encryptedprivatekey',
                    help_text='The EncryptedPrivateKey used to produce the cryptographic signature.',
                )),
            ],
            options={
                'verbose_name': 'Constitution signature',
                'verbose_name_plural': 'Constitution signatures',
                'ordering': ['added_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='constitutionsignature',
            constraint=models.UniqueConstraint(
                fields=['constitution', 'person'],
                name='unique_constitution_signature',
            ),
        ),
    ]
