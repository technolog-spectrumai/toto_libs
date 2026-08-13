"""The ledger learns to carry notes — beside itself, never inside itself.

Three new tables and NOT ONE AlterField on an existing model. That is the whole
design in one line: `LedgerTransaction.description` and `.metadata` are inputs to
the hash chain, so a decoration that touched them would invalidate
`verify_hash_chain()` for every later row. Nothing here can reach the chain,
because `calculate_transaction_hash` reads only the columns it names.

Tags and comments are two independent groups with no relation between them, so a
router can send them to different databases later — Django refuses relations that
span databases, and a join here would be the thing that made the split
impossible.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('assets', '0004_remove_assetsquotapolicy_assets_assetsquotapolicy_one_default_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='LedgerTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Without the '#' — write 'invoice', not '#invoice'.", max_length=50)),
                ('slug', models.SlugField(blank=True, max_length=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(help_text='Whose vocabulary this is. Tags never cross accounts.', on_delete=django.db.models.deletion.CASCADE, related_name='ledger_tags', to='assets.ledgeraccount')),
            ],
            options={
                'verbose_name': 'ledger tag',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='LedgerEntryTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entry_id', models.PositiveBigIntegerField(db_index=True, help_text='assets.LedgerEntry pk. Not an FK — see the class docstring.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entry_links', to='assets.ledgertag')),
            ],
            options={
                'verbose_name': 'ledger entry tag',
                'ordering': ['tag__name'],
            },
        ),
        migrations.CreateModel(
            name='LedgerEntryComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entry_id', models.PositiveBigIntegerField(help_text='assets.LedgerEntry pk. Not an FK — see LedgerEntryTag.', unique=True)),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'ledger entry comment',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='ledgertag',
            constraint=models.UniqueConstraint(fields=('account', 'slug'), name='assets_ledgertag_slug_per_account'),
        ),
        migrations.AddConstraint(
            model_name='ledgertag',
            constraint=models.UniqueConstraint(fields=('account', 'name'), name='assets_ledgertag_name_per_account'),
        ),
        migrations.AddIndex(
            model_name='ledgerentrytag',
            index=models.Index(fields=['tag', 'entry_id'], name='assets_ledg_tag_id_062f95_idx'),
        ),
        migrations.AddConstraint(
            model_name='ledgerentrytag',
            constraint=models.UniqueConstraint(fields=('entry_id', 'tag'), name='assets_ledgerentrytag_once'),
        ),
    ]
