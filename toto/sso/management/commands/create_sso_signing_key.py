from django.core.management.base import BaseCommand, CommandError

from toto.core.models import Platform
from toto.gervazy.models import RSAKeyPair


class Command(BaseCommand):
    help = "Create an RSA signing key and attach it to the active Platform as api_keypair_out."

    def add_arguments(self, parser):
        parser.add_argument("--key-id", required=True, help="Example: sso-main-2026-05")
        parser.add_argument("--issuer", default=None, help="Defaults to active Platform.domain")

    def handle(self, *args, **options):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise CommandError("No active Platform found.")

        issuer = options["issuer"] or platform.domain
        if not issuer:
            raise CommandError("No issuer provided and active Platform.domain is empty.")

        if RSAKeyPair.objects.filter(key_id=options["key_id"]).exists():
            raise CommandError(f"RSAKeyPair already exists with key_id={options['key_id']}")

        keypair = RSAKeyPair.generate(key_id=options["key_id"], issuer=issuer)
        keypair.save()

        platform.api_keypair_out = keypair
        platform.save(update_fields=["api_keypair_out"])

        self.stdout.write(self.style.SUCCESS("SSO signing key created and attached to active Platform."))
        self.stdout.write(f"key_id: {keypair.key_id}")
        self.stdout.write(f"issuer: {issuer}")
