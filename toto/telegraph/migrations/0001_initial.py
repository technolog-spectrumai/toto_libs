from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _rename_enigma_tables(apps, schema_editor):
    """Rename enigma tables to telegraph tables when upgrading from toto.enigma."""
    from django.db import connection
    existing = set(connection.introspection.table_names())
    renames = [
        ("enigma_room",              "telegraph_telegraphchannel"),
        ("enigma_participant",        "telegraph_telegraphmember"),
        ("enigma_room_participants",  "telegraph_telegraphchannel_participants"),
    ]
    with connection.cursor() as cursor:
        for old, new in renames:
            if old in existing and new not in existing:
                cursor.execute(f"ALTER TABLE {old} RENAME TO {new}")


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('people', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_rename_enigma_tables, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name='TelegraphMember',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('joined_at', models.DateTimeField(auto_now_add=True)),
                        ('is_active', models.BooleanField(default=True)),
                        ('person', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='telegraph_memberships', to='people.person')),
                    ],
                    options={
                        'ordering': ['person__display_name'],
                    },
                ),
                migrations.CreateModel(
                    name='TelegraphChannel',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.CharField(max_length=100, unique=True)),
                        ('slug', models.SlugField(unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ('participants', models.ManyToManyField(blank=True, related_name='telegraph_channels', to=settings.AUTH_USER_MODEL)),
                        ('people', models.ManyToManyField(blank=True, related_name='telegraph_channels', through='telegraph.TelegraphMember', to='people.person')),
                    ],
                    options={
                        'ordering': ['name'],
                    },
                ),
                migrations.AddField(
                    model_name='telegraphmember',
                    name='channel',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='telegraph_members', to='telegraph.telegraphchannel'),
                ),
                migrations.AddConstraint(
                    model_name='telegraphmember',
                    constraint=models.UniqueConstraint(condition=models.Q(('person__isnull', False)), fields=('channel', 'person'), name='unique_telegraph_channel_member'),
                ),
                migrations.AddConstraint(
                    model_name='telegraphmember',
                    constraint=models.CheckConstraint(check=models.Q(('person__isnull', False)), name='telegraph_member_must_have_person'),
                ),
            ],
        ),
    ]
