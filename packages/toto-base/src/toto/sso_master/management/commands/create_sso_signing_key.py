"""
Generate an RSA signing key pair for SSO ID tokens.

The private key is encrypted and stored in Gervazy (EncryptedPrivateKey).
The public key is stored in plaintext in SSOSigningKey for fast JWKS responses.

Setup required before first run:
  1. Set SSO_VAULT_PASSWORD in your environment.
  2. Ensure at least one active Django User exists (used as the vault owner).

Usage:
  python manage.py create_sso_signing_key --key-id sso-main-2026-05 --vault-owner admin
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from toto.gervazy.crypto import GervazyCryptoSession
from toto.gervazy.models import UserStrongbox, WrappedDataKey
from toto.sso_master.models import SSOSigningKey

User = get_user_model()

_SSO_STRONGBOX_NAME = "sso-system-strongbox"


class Command(BaseCommand):
    help = "Generate an RSA signing key for SSO and store it encrypted in Gervazy."

    def add_arguments(self, parser):
        parser.add_argument("--key-id", required=True, help="Unique key ID, e.g. sso-main-2026-05")
        parser.add_argument("--key-size", type=int, default=2048, choices=[2048, 4096])
        parser.add_argument("--vault-owner", default="admin", help="Username of the strongbox owner user")
        parser.add_argument("--vault-password", default="", help="Strongbox password (overrides SSO_VAULT_PASSWORD setting)")

    def handle(self, *args, **options):
        from django.conf import settings

        key_id = options["key_id"]
        if SSOSigningKey.objects.filter(key_id=key_id).exists():
            raise CommandError(f"SSOSigningKey already exists with key_id={key_id!r}")

        password = options["vault_password"] or getattr(settings, "SSO_VAULT_PASSWORD", "").strip()
        if not password:
            raise CommandError(
                "No vault password provided. Set SSO_VAULT_PASSWORD in your environment "
                "or pass --vault-password."
            )

        try:
            owner = User.objects.get(username=options["vault_owner"])
        except User.DoesNotExist:
            raise CommandError(f"User {options['vault_owner']!r} not found.")

        # Get or create the SSO system strongbox.
        strongbox = UserStrongbox.objects.filter(name=_SSO_STRONGBOX_NAME).first()
        if strongbox is None:
            self.stdout.write(f"Creating SSO system strongbox: {_SSO_STRONGBOX_NAME!r}")
            session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
                owner, _SSO_STRONGBOX_NAME, password
            )
        else:
            self.stdout.write(f"Using existing SSO system strongbox: {_SSO_STRONGBOX_NAME!r}")
            session = GervazyCryptoSession(strongbox, password)
            wrapped_key = WrappedDataKey.objects.filter(strongbox=strongbox, state="active").first()
            if wrapped_key is None:
                raise CommandError("SSO strongbox has no active WrappedDataKey. The strongbox may be corrupt.")

        # Generate RSA key pair
        self.stdout.write(f"Generating RSA-{options['key_size']} key pair...")
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

        # Encrypt and store the private key via Gervazy
        aad = f"sso-signing-key:{key_id}".encode()
        epk = session.encrypt_private_key(
            wrapped_key,
            private_pem,
            key_id=key_id,
            key_type=f"RSA-{options['key_size']}",
            public_key_pem=public_pem,
            issuer="",
            aad=aad,
        )

        # Retire previous active signing keys
        SSOSigningKey.objects.filter(is_active=True).update(is_active=False)

        SSOSigningKey.objects.create(
            key_id=key_id,
            public_key_pem=public_pem,
            encrypted_key=epk,
            is_active=True,
        )

        self.stdout.write(self.style.SUCCESS(f"SSO signing key created: {key_id}"))
        self.stdout.write(self.style.SUCCESS(
            "The private key is encrypted in Gervazy. "
            "Keep SSO_VAULT_PASSWORD secure and never commit it."
        ))
