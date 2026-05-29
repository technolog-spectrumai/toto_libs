from django.apps import AppConfig


class MeteringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.metering"
    label = "metering"
    verbose_name = "Metering"
