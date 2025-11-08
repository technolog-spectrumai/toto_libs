import random
import uuid
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.management.base import CommandError
from django.urls import reverse

from federal.models import Federation, FederatedIdentity
from oya.models import Platform
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the platform with fake federal data: federations and federated identities"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Federations",
            icon="fa-solid fa-landmark",
            description="Federations and federated identities.",
            link=reverse("federal:current_federation"),
            public=False
        )
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
        federation.head_identity = identities[0]  # optional field if you want a "head"
        federation.save()

        self.stdout.write(self.style.SUCCESS("✅ Federal ingress complete."))

    def create_federation(self, name: str) -> Federation:
        # 🔎 Find an active platform
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise CommandError("No active Platform found. Cannot create Federation.")

        # Create or get federation
        federation, created = Federation.objects.get_or_create(
            name=name,
            defaults={
                "slug": name.lower().replace(" ", "-"),
                "description": f"{name} description",
                "active": True,
                "platform": platform,  # ✅ attach active platform
            }
        )

        # If federation existed but had no platform, attach it
        if not federation.platform_id:
            federation.platform = platform
            federation.save()

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
        head_identity = FederatedIdentity.objects.create(
            id=uuid.uuid4(),
            name="Head Identity",
            created_at=timezone.now(),
            federation=federation
        )
        identities.append(head_identity)

        # Other identities
        for i in range(1, count):
            identity = FederatedIdentity.objects.create(
                id=uuid.uuid4(),
                name=f"Identity {i}",
                created_at=timezone.now(),
                federation=federation
            )
            identities.append(identity)

        return identities
