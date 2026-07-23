# GIS-off variant of locations/migrations/0001_initial.py.
#
# Selected via MIGRATION_MODULES when BUILD_GEO=0 (see the suite README,
# "Making GIS optional"). Creates the same seven tables with the SAME node name
# ('locations','0001_initial') — so every cross-app dependency resolves
# unchanged — but WITHOUT any geometry columns and WITHOUT importing
# django.contrib.gis (no GDAL/GEOS needed). Keep this in lockstep with the
# GIS-on graph: only the geometry/center fields differ.

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Address',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('country_name', models.CharField(blank=True, max_length=2, verbose_name='Country')),
                ('state_or_province_name', models.CharField(blank=True, max_length=128, verbose_name='State/Province')),
                ('locality_name', models.CharField(blank=True, max_length=128, verbose_name='Locality')),
                ('street', models.CharField(blank=True, max_length=255, verbose_name='Street')),
                ('building', models.CharField(blank=True, max_length=64, verbose_name='Building Number')),
                ('apartment', models.CharField(blank=True, max_length=64, null=True, verbose_name='Apartment Number')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='MapLayer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=220, unique=True)),
                ('description', models.TextField(blank=True)),
                ('unit', models.CharField(blank=True, help_text='Examples: °C, %, mm, ppm, people/km²', max_length=32)),
                ('min_value', models.FloatField(blank=True, help_text='Optional display minimum for color scaling.', null=True)),
                ('max_value', models.FloatField(blank=True, help_text='Optional display maximum for color scaling.', null=True)),
                ('style', models.JSONField(blank=True, default=dict, help_text='Frontend style config: color scale, opacity, legend, etc.')),
                ('inverted_importance', models.BooleanField(default=False, help_text='Invert value importance before color scaling.')),
                ('half_range', models.BooleanField(default=False, help_text='Use only the low-to-mid half of the color scale.')),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RouteChain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Territory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('capital', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='territory_capitals', to='locations.address')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Zone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('territory', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='zones', to='locations.territory')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Route',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(blank=True, max_length=200)),
                ('sequence', models.PositiveIntegerField(default=0)),
                ('end_address', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='route_ends', to='locations.address')),
                ('route_chain', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='routes', to='locations.routechain')),
                ('start_address', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='route_starts', to='locations.address')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='MapLayerPolygon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(blank=True, max_length=200)),
                ('value', models.FloatField()),
                ('properties', models.JSONField(blank=True, default=dict, help_text='Optional metadata for frontend/domain use.')),
                ('layer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='polygons', to='locations.maplayer')),
            ],
        ),
    ]
