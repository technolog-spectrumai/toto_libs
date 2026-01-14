import os
import random
import uuid
from django.conf import settings
from django.core.files import File
from django.core.management.base import CommandError
from django.contrib.auth.models import User
from django.utils import timezone

from federal.models import Federation, FederatedIdentity, IdentityProfile
from gervazy.models import RSAKeyPair
from oya.models import Platform
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the platform with fake federal data: federations, federated identities, and identity profiles"

    def process(self):
        if not self.full:
            return

        self.stdout.write(self.style.NOTICE("🏛️ Creating federations..."))
        try:
            federation = self.create_federation("Global Federation")
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"❌ {e}"))
            return

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating federated identities..."))
        identities = self.create_fake_identities(count=5)

        self.stdout.write(self.style.NOTICE("🪪 Creating identity profiles..."))
        profiles = self.create_identity_profiles(identities)

        self.stdout.write(self.style.SUCCESS("✅ Federal ingress complete."))

    # ---------------------------------------------------------
    # Federation creation
    # ---------------------------------------------------------

    def create_federation(self, name: str) -> Federation:
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise CommandError("No active Platform found. Cannot create Federation.")

        federation, created = Federation.objects.get_or_create(
            name=name,
            defaults={
                "slug": name.lower().replace(" ", "-"),
                "description": f"{name} description",
                "active": True,
                "platform": platform,
            }
        )

        if federation.platform_id is None:
            federation.platform = platform
            federation.save()

        logo_path = os.path.abspath(
            os.path.join(settings.BASE_DIR, "..", "data", "img", "logo.png")
        )

        if not os.path.exists(logo_path):
            raise CommandError(f"Logo file not found at {logo_path}")

        if not federation.logo:
            with open(logo_path, "rb") as f:
                federation.logo.save("logo.png", File(f), save=True)

        return federation

    # ---------------------------------------------------------
    # Federated Identity creation (minimal model)
    # ---------------------------------------------------------

    def create_fake_identities(self, count=5):
        identities = []

        users = [
            User.objects.get_or_create(
                username=f"federal_user{i}",
                defaults={"email": f"federal_user{i}@example.com"}
            )[0]
            for i in range(count)
        ]

        for i in range(count):
            identity = self._create_identity_with_rsa(
                name=f"Identity {i}",
                user=random.choice(users)
            )
            identities.append(identity)

        return identities

    def _create_identity_with_rsa(self, name: str, user: User) -> FederatedIdentity:
        identity = FederatedIdentity.objects.create(
            id=uuid.uuid4(),
            name=name,
            user=user,
            created_at=timezone.now(),
        )

        key_id = f"{identity.id}-key"
        platform = Platform.objects.filter(active=True).first()
        issuer = platform.issuer_url
        rsa_pair = RSAKeyPair.generate(key_id=key_id, issuer=issuer)
        rsa_pair.save()

        identity.rsa_keypair = rsa_pair
        identity.save()

        return identity

    # ---------------------------------------------------------
    # IdentityProfile creation + linking to FederatedIdentity
    # ---------------------------------------------------------

    def create_identity_profiles(self, identities):
        profiles = []

        for identity in identities:
            profile = IdentityProfile.objects.create(
                legal_name=identity.name or f"Identity {identity.id}",
                profile_type=IdentityProfile.INDIVIDUAL,
                registration_number=str(uuid.uuid4())[:12],
                registration_type="Auto-generated",
                is_verified=False,
                metadata={
                    "federated_identity": str(identity.id),
                }
            )

            # Link FederatedIdentity → IdentityProfile
            identity.profile = profile
            identity.save()

            profiles.append(profile)

        return profiles
