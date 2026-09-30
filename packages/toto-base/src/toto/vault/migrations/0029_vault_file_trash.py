# A trash for vault files (2026-10-01), schema only.
#
# - VaultFile.trashed_at / trashed_by / trashed_from: when, by whom, and the
#   folder it left (for the restore).
# - The (bucket, key) rule now binds LIVE files only: a trashed file keeps its
#   key, and a new upload of the same name must still work.
# - Managers: the default one hides the trash; ``all_objects`` is the base
#   manager. Historical models get two plain managers, so a later data
#   migration sees every row.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.db.models.manager


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('vault', '0028_bucket_management'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='vaultfile',
            options={'base_manager_name': 'all_objects', 'verbose_name': 'Vault File', 'verbose_name_plural': 'Vault Files'},
        ),
        migrations.AlterModelManagers(
            name='vaultfile',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='vaultfile',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='vaultfile',
            name='trashed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='vaultfile',
            name='trashed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='vaultfile',
            name='trashed_from',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='vault.vaultdirectory'),
        ),
        migrations.AddConstraint(
            model_name='vaultfile',
            constraint=models.UniqueConstraint(condition=models.Q(('trashed_at__isnull', True)), fields=('bucket', 'key'), name='vault_one_live_file_per_key'),
        ),
    ]
