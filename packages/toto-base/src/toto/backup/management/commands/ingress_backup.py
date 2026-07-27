from django.conf import settings
from django.contrib.auth import get_user_model

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from toto.backup.models import BackupProfile
from toto.core.models import Platform
from toto.gervazy.crypto import GervazyCryptoSession
from toto.gervazy.models import EncryptedPrivateKey, UserStrongbox, WrappedDataKey
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed BackupProfile records and a development backup signing key when possible."

    def process(self):
        if not self.full:
            return

        platforms = list(Platform.objects.all())
        if not platforms:
            self.stdout.write(self.style.WARNING("No Platform found. Skipping backup profile ingress."))
            return

        signing_key = self.get_or_create_signing_key()
        for platform in platforms:
            profile, created = BackupProfile.objects.get_or_create(platform=platform)
            changed = False

            if signing_key and not profile.signing_key_id:
                profile.signing_key = signing_key
                changed = True
            if signing_key and not profile.verify_key:
                profile.verify_key = signing_key.public_key_pem
                changed = True

            if changed:
                profile.save(update_fields=["signing_key", "verify_key"])

            status = "Created" if created else "Updated" if changed else "Exists"
            self.stdout.write(self.style.SUCCESS(f"{status} BackupProfile for {platform.site_name}"))

        if not signing_key:
            self.stdout.write(
                self.style.WARNING(
                    "Backup profiles were created without signing keys. "
                    "Set BACKUP_VAULT_PASSWORD or SSO_VAULT_PASSWORD to seed an encrypted signing key."
                )
            )

    def get_or_create_signing_key(self):
        existing = EncryptedPrivateKey.objects.filter(key_id="backup-signing-dev").first()
        if existing:
            return existing

        password = (
            getattr(settings, "BACKUP_VAULT_PASSWORD", "")
            or getattr(settings, "SSO_VAULT_PASSWORD", "")
        )
        if not password:
            return None

        user = self.get_owner()
        try:
            session, wrapped_key = self.get_or_create_crypto_session(user, password)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Could not seed backup signing key: {exc}"))
            return None
        private_pem, public_pem = self.generate_rsa_keypair()

        return session.encrypt_private_key(
            wrapped_key,
            private_pem,
            key_id="backup-signing-dev",
            key_type="RSA-2048",
            public_key_pem=public_pem,
            issuer="",
            aad=b"toto.backup.signing.dev",
        )

    def get_owner(self):
        User = get_user_model()
        return (
            User.objects.filter(username="admin").first()
            or User.objects.filter(is_superuser=True).first()
            or User.objects.order_by("id").first()
        )

    def get_or_create_crypto_session(self, user, password):
        if user is None:
            raise RuntimeError("Cannot seed backup signing key without a user.")

        wrapped_key = (
            WrappedDataKey.objects
            .filter(strongbox__owner=user, state="active", vmk__state="active")
            .select_related("strongbox", "vmk")
            .first()
        )
        if wrapped_key:
            return GervazyCryptoSession(wrapped_key.strongbox, password), wrapped_key

        strongbox_name = "Backup Signing Strongbox"
        existing_strongbox = UserStrongbox.objects.filter(owner=user, name=strongbox_name).first()
        if existing_strongbox:
            self.stdout.write(
                self.style.WARNING(
                    f"Strongbox '{strongbox_name}' exists but has no active data key. "
                    "Skipping signing key seed."
                )
            )
            raise RuntimeError("Backup signing strongbox has no active data key.")

        return GervazyCryptoSession.initialize_strongbox(
            owner=user,
            strongbox_name=strongbox_name,
            password=password,
        )

    @staticmethod
    def generate_rsa_keypair():
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return private_pem, public_pem
