from django.core.management.base import BaseCommand

from toto.nomad import service


class Command(BaseCommand):
    help = "Print the current faros onion address (used by deploy.py onion)."

    def handle(self, *args, **options):
        onion = service.current_onion()
        if onion:
            self.stdout.write(f"{onion}.onion")
