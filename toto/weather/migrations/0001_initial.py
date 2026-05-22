import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("locations", "0001_initial"),
        ("workflows", "0009_workflow_spine_redesign"),
    ]

    operations = [
        migrations.CreateModel(
            name="WeatherSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_provider", models.CharField(
                    choices=[("open_meteo", "Open-Meteo (Free)")],
                    default="open_meteo",
                    max_length=50,
                    verbose_name="Current weather provider",
                )),
                ("forecast_provider", models.CharField(
                    choices=[("open_meteo", "Open-Meteo (Free)")],
                    default="open_meteo",
                    max_length=50,
                    verbose_name="Forecast provider",
                )),
            ],
            options={"verbose_name": "Weather Settings", "verbose_name_plural": "Weather Settings"},
        ),
        migrations.CreateModel(
            name="ForecastSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("open_meteo", "Open-Meteo (Free)")], max_length=50)),
                ("loaded_at", models.DateTimeField(auto_now_add=True)),
                ("valid_from", models.DateTimeField()),
                ("valid_to", models.DateTimeField()),
                ("resolution_hours", models.IntegerField(
                    default=1,
                    help_text="Forecast time resolution in hours, determined by the provider.",
                )),
                ("workflow_run", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="forecast_sessions",
                    to="workflows.workflowrun",
                )),
            ],
            options={"ordering": ["-loaded_at"]},
        ),
        migrations.CreateModel(
            name="WeatherObservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("open_meteo", "Open-Meteo (Free)")], max_length=50)),
                ("observed_at", models.DateTimeField()),
                ("loaded_at", models.DateTimeField(auto_now_add=True)),
                ("temperature", models.FloatField(blank=True, help_text="°C", null=True)),
                ("precipitation_mm", models.FloatField(blank=True, help_text="mm", null=True)),
                ("precipitation_type", models.CharField(
                    blank=True,
                    choices=[("", "None"), ("rain", "Rain"), ("drizzle", "Drizzle"), ("snow", "Snow"), ("sleet", "Sleet"), ("hail", "Hail")],
                    max_length=20,
                )),
                ("cloud_cover", models.IntegerField(blank=True, help_text="%", null=True)),
                ("wind_speed_kmh", models.FloatField(blank=True, help_text="km/h", null=True)),
                ("wind_direction_deg", models.IntegerField(blank=True, help_text="degrees", null=True)),
                ("visibility_km", models.FloatField(blank=True, help_text="km", null=True)),
                ("address", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="weather_observations",
                    to="locations.address",
                )),
                ("workflow_run", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="weather_observations",
                    to="workflows.workflowrun",
                )),
            ],
            options={"ordering": ["-loaded_at"]},
        ),
        migrations.CreateModel(
            name="ForecastPoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("valid_at", models.DateTimeField()),
                ("temperature", models.FloatField(blank=True, help_text="°C", null=True)),
                ("precipitation_mm", models.FloatField(blank=True, help_text="mm", null=True)),
                ("precipitation_type", models.CharField(
                    blank=True,
                    choices=[("", "None"), ("rain", "Rain"), ("drizzle", "Drizzle"), ("snow", "Snow"), ("sleet", "Sleet"), ("hail", "Hail")],
                    max_length=20,
                )),
                ("cloud_cover", models.IntegerField(blank=True, help_text="%", null=True)),
                ("wind_speed_kmh", models.FloatField(blank=True, help_text="km/h", null=True)),
                ("wind_direction_deg", models.IntegerField(blank=True, help_text="degrees", null=True)),
                ("visibility_km", models.FloatField(blank=True, help_text="km", null=True)),
                ("address", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="forecast_points",
                    to="locations.address",
                )),
                ("session", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="points",
                    to="weather.forecastsession",
                )),
            ],
            options={"ordering": ["valid_at"]},
        ),
        migrations.AddIndex(
            model_name="forecastpoint",
            index=models.Index(fields=["session", "address", "valid_at"], name="weather_for_session_fd3c6c_idx"),
        ),
    ]
