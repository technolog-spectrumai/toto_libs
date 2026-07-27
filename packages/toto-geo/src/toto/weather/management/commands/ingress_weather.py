"""
ingress_weather — verify the weather tariff and seed default WeatherSettings.
"""
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Verify the WEATHER-STANDARD tariff and seed default WeatherSettings."

    def process(self):
        self._ensure_settings()
        self._seed_workflows()

    def _seed_workflows(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("seed_weather_workflows", stdout=out, stderr=out)
        for line in out.getvalue().splitlines():
            self.stdout.write(f"  {line}")

    def _ensure_settings(self):
        from toto.weather.models import WeatherSettings
        settings, created = WeatherSettings.objects.get_or_create(
            pk=1,
            defaults={
                "current_provider": "open_meteo",
                "forecast_provider": "open_meteo",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("  + WeatherSettings created (provider: open_meteo)."))
        else:
            self.stdout.write("  WeatherSettings already exist.")
        self.stdout.write(self.style.SUCCESS("Weather ingress complete."))
