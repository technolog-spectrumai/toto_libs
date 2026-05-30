"""
ingress_weather — verify the weather tariff and seed default WeatherSettings.
"""
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Verify the WEATHER-STANDARD tariff and seed default WeatherSettings."

    def process(self):
        self._check_tariff()
        self._ensure_settings()

    def _check_tariff(self):
        from django.apps import apps as django_apps
        if not django_apps.is_installed("toto.tariffs"):
            self.stdout.write("  toto.tariffs not installed — skipping tariff check.")
            return
        from toto.tariffs.models import Tariff
        if Tariff.objects.filter(code="WEATHER-STANDARD").exists():
            self.stdout.write("  [weather] WEATHER-STANDARD tariff: ready.")
        else:
            self.stdout.write(self.style.WARNING(
                "  [weather] WEATHER-STANDARD tariff not found — run ingress_tariffs first."
            ))

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
