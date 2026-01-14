from django.utils import timezone
from django.core.files.base import ContentFile
from oya.ingress import IngressCommand
from assets.models import AssetType, Asset, AssetImage, FractionalOwnership
from federal.models import IdentityProfile
from community.models import CommunityMember, Community
from locations.models import Address
import random
import base64
from faker import Faker
from django.urls import reverse

fake = Faker()


class Command(IngressCommand):
    help = "Seeds demo asset types, assets, identity profiles, and fractional ownership."

    def process(self):

        self.create_dashboard_item(
            title="Assets",
            icon="fa-solid fa-boxes-stacked",
            description="Asset registry with ownership, types, and identity profiles.",
            link=reverse("assets:asset_list"),
            public=False,
        )

        if not self.full:
            return

        # ---------------------------------------------------------
        # SAFETY: Prevent duplicate demo data
        # ---------------------------------------------------------
        if AssetType.objects.filter(name="Vehicle").exists():
            print("[Ingress] Asset demo already exists — skipping.")
            return

        # ---------------------------------------------------------
        # COMMUNITY + ADDRESS REQUIREMENTS
        # ---------------------------------------------------------
        members = list(CommunityMember.objects.all())
        communities = list(Community.objects.all())
        addresses = list(Address.objects.all())

        if not members:
            raise Exception("❌ Need at least 1 CommunityMember to seed assets.")
        if not communities:
            raise Exception("❌ Need at least 1 Community to seed assets.")
        if not addresses:
            raise Exception("❌ Need at least 1 Address to seed assets.")

        member = random.choice(members)
        community = random.choice(communities)
        address = random.choice(addresses)

        # ---------------------------------------------------------
        # ASSET TYPES
        # ---------------------------------------------------------
        vehicle = AssetType.objects.create(name="Vehicle", description="Cars, trucks, and transport assets")
        equipment = AssetType.objects.create(name="Equipment", description="Tools and machinery")
        real_estate = AssetType.objects.create(name="Real Estate", description="Buildings and land")

        # ---------------------------------------------------------
        # FIND EXISTING IDENTITY PROFILES
        # ---------------------------------------------------------
        existing_profiles = list(IdentityProfile.objects.all())

        if len(existing_profiles) >= 2:
            owner_individual = existing_profiles[0]
            owner_org = existing_profiles[1]
        else:
            # ---------------------------------------------------------
            # FALLBACK: Create minimal valid profiles
            # ---------------------------------------------------------
            owner_individual = IdentityProfile.objects.create(
                profile_type=IdentityProfile.INDIVIDUAL,
                legal_name=str(member),
                registration_number=fake.ssn(),
                registration_type="National ID",
                is_verified=True,
                verified_at=timezone.now(),
                verified_by=member,
                metadata={"source": "auto-ingress"},
            )

            owner_org = IdentityProfile.objects.create(
                profile_type=IdentityProfile.ORGANIZATION,
                legal_name=f"{community.name} Holdings",
                registration_number="REG-998877",
                registration_type="Tax ID",
                is_verified=True,
                verified_at=timezone.now(),
                verified_by=member,
                metadata={"source": "auto-ingress"},
            )

        # ---------------------------------------------------------
        # ASSETS
        # ---------------------------------------------------------
        car = Asset.objects.create(
            name="Toyota Hilux",
            asset_type=vehicle,
            description="4x4 utility vehicle",
            serial_number="CAR-001",
            purchase_date=timezone.now().date(),
            purchase_price=35000,
            assigned_to=member,
            location=address,
        )

        drill = Asset.objects.create(
            name="Industrial Drill",
            asset_type=equipment,
            description="Heavy-duty drilling machine",
            serial_number="EQ-4455",
            purchase_date=timezone.now().date(),
            purchase_price=12000,
            assigned_to=None,
            location=address,
        )

        land = Asset.objects.create(
            name="Plot 22B",
            asset_type=real_estate,
            description="2-acre land parcel",
            serial_number="LAND-22B",
            purchase_price=150000,
            assigned_to=None,
            location=address,
        )

        # ---------------------------------------------------------
        # FRACTIONAL OWNERSHIP
        # ---------------------------------------------------------
        FractionalOwnership.objects.create(
            asset=land,
            owner=owner_individual,
            percentage=40
        )

        FractionalOwnership.objects.create(
            asset=land,
            owner=owner_org,
            percentage=60
        )

        # ---------------------------------------------------------
        # OPTIONAL DEMO IMAGES
        # ---------------------------------------------------------
        placeholder_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )

        AssetImage.objects.create(
            asset=car,
            caption="Front view",
            order=1,
            image=ContentFile(placeholder_png, name="car.png")
        )

        AssetImage.objects.create(
            asset=drill,
            caption="Machine photo",
            order=1,
            image=ContentFile(placeholder_png, name="drill.png")
        )

        print("[Ingress] Demo asset registry created successfully.")
