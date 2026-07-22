from django.db import models


PROVIDER_CHOICES = [
    ("open_meteo", "Open-Meteo (Free)"),
]

PRECIPITATION_TYPE_CHOICES = [
    ("", "None"),
    ("rain", "Rain"),
    ("drizzle", "Drizzle"),
    ("snow", "Snow"),
    ("sleet", "Sleet"),
    ("hail", "Hail"),
]


class WeatherSettings(models.Model):
    """Singleton configuration for the weather app."""

    current_provider = models.CharField(
        max_length=50,
        choices=PROVIDER_CHOICES,
        default="open_meteo",
        verbose_name="Current weather provider",
    )
    forecast_provider = models.CharField(
        max_length=50,
        choices=PROVIDER_CHOICES,
        default="open_meteo",
        verbose_name="Forecast provider",
    )

    class Meta:
        verbose_name = "Weather Settings"
        verbose_name_plural = "Weather Settings"

    def __str__(self):
        return f"Weather Settings (current={self.current_provider}, forecast={self.forecast_provider})"

    @classmethod
    def get(cls) -> "WeatherSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class WeatherObservation(models.Model):
    """Current weather conditions at a specific address, loaded by a workflow run."""

    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.CASCADE,
        related_name="weather_observations",
    )
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weather_observations",
    )
    observed_at = models.DateTimeField()
    loaded_at = models.DateTimeField(auto_now_add=True)

    temperature = models.FloatField(null=True, blank=True, help_text="°C")
    precipitation_mm = models.FloatField(null=True, blank=True, help_text="mm")
    precipitation_type = models.CharField(
        max_length=20, blank=True, choices=PRECIPITATION_TYPE_CHOICES
    )
    cloud_cover = models.IntegerField(null=True, blank=True, help_text="%")
    wind_speed_kmh = models.FloatField(null=True, blank=True, help_text="km/h")
    wind_direction_deg = models.IntegerField(null=True, blank=True, help_text="degrees")
    visibility_km = models.FloatField(null=True, blank=True, help_text="km")

    class Meta:
        ordering = ["-loaded_at"]

    def __str__(self):
        return f"{self.address} @ {self.observed_at}"


class ForecastSession(models.Model):
    """A preloaded forecast dataset for a set of locations."""

    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forecast_sessions",
    )
    loaded_at = models.DateTimeField(auto_now_add=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    resolution_hours = models.IntegerField(
        default=1,
        help_text="Forecast time resolution in hours, determined by the provider.",
    )

    class Meta:
        ordering = ["-loaded_at"]

    def __str__(self):
        return f"Forecast {self.valid_from} → {self.valid_to} ({self.provider})"


class ForecastPoint(models.Model):
    """Weather forecast for a single location at a single point in time."""

    session = models.ForeignKey(
        ForecastSession, on_delete=models.CASCADE, related_name="points"
    )
    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.CASCADE,
        related_name="forecast_points",
    )
    valid_at = models.DateTimeField()

    temperature = models.FloatField(null=True, blank=True, help_text="°C")
    precipitation_mm = models.FloatField(null=True, blank=True, help_text="mm")
    precipitation_type = models.CharField(
        max_length=20, blank=True, choices=PRECIPITATION_TYPE_CHOICES
    )
    cloud_cover = models.IntegerField(null=True, blank=True, help_text="%")
    wind_speed_kmh = models.FloatField(null=True, blank=True, help_text="km/h")
    wind_direction_deg = models.IntegerField(null=True, blank=True, help_text="degrees")
    visibility_km = models.FloatField(null=True, blank=True, help_text="km")

    class Meta:
        ordering = ["valid_at"]
        indexes = [
            models.Index(fields=["session", "address", "valid_at"]),
        ]

    def __str__(self):
        return f"{self.address} @ {self.valid_at}"
