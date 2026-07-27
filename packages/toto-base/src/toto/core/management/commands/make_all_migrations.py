from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run makemigrations for every installed toto app individually."

    def add_arguments(self, parser):
        parser.add_argument(
            "--merge",
            action="store_true",
            help="Pass --merge to resolve migration conflicts.",
        )

    def handle(self, *args, **options):
        merge = options["merge"]

        for app_config in apps.get_app_configs():
            if not app_config.name.startswith("toto."):
                continue

            label = app_config.label
            try:
                kwargs = {"interactive": False}
                if merge:
                    kwargs["merge"] = True
                call_command("makemigrations", label, **kwargs)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  skip {label}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
