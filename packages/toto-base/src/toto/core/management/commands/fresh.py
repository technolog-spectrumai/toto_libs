from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Full local reset: wipe DB, migrate, seed platform, run ingress. Dev only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin",
            help="Admin password (default: admin)",
        )
        parser.add_argument(
            "--no-ingress",
            action="store_true",
            help="Skip ingress_all after init.",
        )

    def handle(self, *args, **options):
        from django.conf import settings  # noqa: PLC0415

        if getattr(settings, "DJANGO_ENV", "dev") == "PROD":
            self.stderr.write(self.style.ERROR(
                "Refusing to run 'fresh' in PROD environment. "
                "Use 'init_platform --reset' explicitly if you are sure."
            ))
            return

        self.stdout.write(self.style.WARNING("=== fresh: resetting local environment ==="))
        call_command("init_platform", reset=True, password=options["password"])

        if not options["no_ingress"]:
            call_command("ingress_all")

        self.stdout.write(self.style.SUCCESS("=== Done. ==="))
