# Map domains (2026-09-30): clearances go on groups, never on items. The
# per-item RouteClearance and MapLayerClearance go; MapDomain, its clearance
# rows and one typed membership table per kind come. No geometry: the same
# file in migrations/ and migrations_nogis/.

from django.db import migrations, models
import django.db.models.deletion


def _membership(name, item_field, item_model, rows, constraint):
    return [
        migrations.CreateModel(
            name=name,
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name=rows, to='locations.mapdomain')),
                (item_field, models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='domain_rows', to=item_model)),
            ],
        ),
        migrations.AddConstraint(
            model_name=name.lower(),
            constraint=models.UniqueConstraint(fields=('domain', item_field), name=constraint),
        ),
    ]


class Migration(migrations.Migration):

    dependencies = [
        ('socialhub', '0014_clearances'),
        ('locations', '0008_clearances'),
    ]

    operations = [
        migrations.DeleteModel(name='RouteClearance'),
        migrations.DeleteModel(name='MapLayerClearance'),
        migrations.CreateModel(
            name='MapDomain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=140, unique=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='MapDomainClearance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clearance', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='map_domain_rows', to='socialhub.clearance')),
                ('domain', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='clearance_rows', to='locations.mapdomain')),
            ],
        ),
        migrations.AddConstraint(
            model_name='mapdomainclearance',
            constraint=models.UniqueConstraint(fields=('domain', 'clearance'), name='locations_domain_clearance_once'),
        ),
        *_membership('RouteInDomain', 'route', 'locations.route', 'route_rows',
                     'locations_route_in_domain_once'),
        *_membership('MapLayerInDomain', 'map_layer', 'locations.maplayer', 'map_layer_rows',
                     'locations_layer_in_domain_once'),
        *_membership('AddressInDomain', 'address', 'locations.address', 'address_rows',
                     'locations_address_in_domain_once'),
        *_membership('ZoneInDomain', 'zone', 'locations.zone', 'zone_rows',
                     'locations_zone_in_domain_once'),
        *_membership('TerritoryInDomain', 'territory', 'locations.territory', 'territory_rows',
                     'locations_territory_in_domain_once'),
    ]
