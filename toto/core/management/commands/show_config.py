import os
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print the active TOTO_CONFIG and key resolved settings."

    def handle(self, *args, **options):
        config_path = os.environ.get("TOTO_CONFIG", "(not set)")
        self.stdout.write(f"\nConfig file : {config_path}")
        self.stdout.write(f"DJANGO_ENV  : {getattr(settings, 'DJANGO_ENV', '?')}")
        self.stdout.write(f"DEBUG       : {settings.DEBUG}")
        self.stdout.write(f"DB engine   : {settings.DATABASES['default']['ENGINE']}")
        db = settings.DATABASES["default"]
        if "NAME" in db:
            self.stdout.write(f"DB name     : {db['NAME']}")
        if "HOST" in db:
            self.stdout.write(f"DB host     : {db['HOST']}:{db.get('PORT', '')}")
        self.stdout.write(f"\nInstalled apps ({len(settings.INSTALLED_APPS)}):")
        for app in settings.INSTALLED_APPS:
            self.stdout.write(f"  {app}")
        self.stdout.write("")
