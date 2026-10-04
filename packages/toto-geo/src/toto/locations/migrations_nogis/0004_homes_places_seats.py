# GIS-off variant of locations/migrations/0004_homes_places_seats.py. Keep it in
# lockstep with the GIS-on graph: only geometry fields may differ, and this one
# has none (the links from people, events and communities to the map,
# 2026-10-04).

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0002_initial'),
        ('events', '0001_initial'),
        ('socialhub', '0002_initial'),
        ('locations', '0003_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Home',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sharing', models.CharField(choices=[('off', 'Not shown to anyone'), ('approximate', 'Approximate area only'), ('exact', 'Exact address')], default='off', max_length=12)),
                ('address', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='homes', to='locations.address')),
                ('person', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='home', to='people.person')),
            ],
        ),
        migrations.CreateModel(
            name='EventPlace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('address', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='event_places', to='locations.address')),
                ('event', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='place_on_map', to='events.scheduledevent')),
            ],
        ),
        migrations.CreateModel(
            name='CommunitySeat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('address', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='community_seats', to='locations.address')),
                ('community', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='seat_on_map', to='socialhub.community')),
                ('territory', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='community_seats', to='locations.territory')),
            ],
        ),
    ]
