from datetime import datetime

from django.core.management.base import BaseCommand

from toto.core.models import Platform


class Command(BaseCommand):
    help = "Create a minimal active Platform if none exists."

    def add_arguments(self, parser):
        parser.add_argument("--site-name", default="Toto Portal")
        parser.add_argument("--domain", default="http://localhost:8080")
        parser.add_argument("--author", default="Toto")

    def handle(self, *args, **options):
        active_platform = Platform.objects.filter(active=True).first()
        if active_platform:
            self.stdout.write(
                self.style.SUCCESS(f"Active Platform exists: {active_platform.site_name}")
            )
            return

        platform = Platform.objects.create(
            site_name=options["site_name"],
            domain=options["domain"],
            author=options["author"],
            publication_year=datetime.now().year,
            active=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Created Platform: {platform.site_name}"))
