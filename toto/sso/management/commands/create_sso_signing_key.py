from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management.base import BaseCommand, CommandError

from toto.sso.models import SSOSigningKey


class Command(BaseCommand):
    help = "Generate an RSA signing key pair for SSO ID tokens and store the public key."

    def add_arguments(self, parser):
        parser.add_argument("--key-id", required=True, help="Unique key ID, e.g. sso-main-2026-05")
        parser.add_argument("--key-size", type=int, default=2048, choices=[2048, 4096])

    def handle(self, *args, **options):
        key_id = options["key_id"]

        if SSOSigningKey.objects.filter(key_id=key_id).exists():
            raise CommandError(f"SSOSigningKey already exists with key_id={key_id!r}")

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=options["key_size"])

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        SSOSigningKey.objects.filter(is_active=True).update(is_active=False)
        SSOSigningKey.objects.create(key_id=key_id, public_key_pem=public_pem, is_active=True)

        self.stdout.write(self.style.SUCCESS(f"SSOSigningKey created: {key_id}"))
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Set this environment variable on your server:"))
        self.stdout.write("")
        self.stdout.write("  SSO_SIGNING_PRIVATE_KEY='<private key below>'")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Private key PEM (store this securely — never commit it):"))
        self.stdout.write(private_pem)
        self.stdout.write(self.style.ERROR("This private key will NOT be shown again. Store it now."))
