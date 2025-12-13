import os
import random
import uuid
from django.conf import settings
from django.core.files import File
from django.core.management.base import CommandError
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from federal.models import Federation, FederatedIdentity
from gervazy.models import RSAKeyPair
from oya.models import Platform
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the platform with fake federal data: federations and federated identities"

    def process(self):
        # 📊 Dashboard block
        # self.create_dashboard_item(
        #     title="Federation",
        #     icon="fa-solid fa-landmark",
        #     description="Federation and federated identities.",
        #     link=reverse("federal:current_federation"),
        #     public=False
        # )
        if not self.full:
            return

        self.stdout.write(self.style.NOTICE("🏛️ Creating federations..."))
        try:
            federation = self.create_federation("Global Federation")
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"❌ {e}"))
            return

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating federated identities..."))
        identities = self.create_fake_identities(federation, count=5)

        self.stdout.write(self.style.NOTICE("👑 Assigning head identity..."))
        federation.head_identity = identities[0]
        federation.save()

        self.stdout.write(self.style.SUCCESS("✅ Federal ingress complete."))

    def create_federation(self, name: str) -> Federation:
        # 🔎 Find an active platform
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

        if not federation.platform_id:
            federation.platform = platform
            federation.save()

        # 📷 Upload logo from data/img/logo.png
        logo_path = os.path.join(settings.BASE_DIR, "..", "data", "img", "logo.png")
        logo_path = os.path.abspath(logo_path)
        if not os.path.exists(logo_path):
            raise CommandError(f"Logo file not found at {logo_path}")

        if not federation.logo:
            with open(logo_path, "rb") as f:
                federation.logo.save("logo.png", File(f), save=True)

        return federation

    def create_fake_identities(self, federation: Federation, count=5):
        identities = []
        users = []

        for i in range(count):
            username = f"federal_user{i}"
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.com"}
            )
            users.append(user)

        # Head identity
        head_identity = self._create_identity_with_rsa(
            federation=federation,
            name="Head Identity",
            user=random.choice(users)
        )
        identities.append(head_identity)

        # Other identities
        for i in range(1, count):
            identity = self._create_identity_with_rsa(
                federation=federation,
                name=f"Identity {i}",
                user=random.choice(users)
            )
            identities.append(identity)

        return identities

    def _create_identity_with_rsa(self, federation: Federation, name: str, user: User) -> FederatedIdentity:
        identity = FederatedIdentity.objects.create(
            id=uuid.uuid4(),
            name=name,
            created_at=timezone.now(),
            federation=federation,
            user=user
        )

        # 🔐 Generate and attach RSA keypair
        key_id = f"{identity.id}-key"
        rsa_pair = RSAKeyPair.generate(key_id=key_id, issuer=identity.issuer)
        rsa_pair.save()
        identity.rsa_keypair = rsa_pair
        identity.save()

        return identity
