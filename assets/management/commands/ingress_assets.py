from django.urls import reverse
from oya.ingress import IngressCommand
from assets.models import Asset, AssetType
from community.models import CommunityMember
from django.contrib.auth.models import User
import random
from faker import Faker
from locations.models import Address

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for assets: asset types, assets linked to community members"

    def create_asset_types(self):
        type_data = [
            ("Laptop", "Portable computer device"),
            ("Vehicle", "Cars, trucks, or other vehicles"),
            ("Furniture", "Office and home furniture"),
            ("Software", "Licensed digital products"),
        ]
        if self.full:
            type_data.extend([
                ("Server", "Rack-mounted or standalone servers"),
                ("Phone", "Mobile devices"),
            ])

        asset_types = []
        for name, description in type_data:
            asset_type, _ = AssetType.objects.get_or_create(
                name=name,
                defaults={"description": description}
            )
            asset_types.append(asset_type)
        return asset_types

    def create_assets(self, members, asset_types):
        assets = []

        for i in range(15):
            addresses = list(Address.objects.all())
            if not addresses:
                raise RuntimeError("No addresses found. Please seed addresses before seeding assets.")
            asset_type = random.choice(asset_types)
            asset = Asset.objects.create(
                name=fake.word().capitalize() + " " + asset_type.name,
                asset_type=asset_type,
                description=fake.sentence(),
                serial_number=fake.uuid4(),
                purchase_date=fake.date_between(start_date="-2y", end_date="today"),
                purchase_price=round(random.uniform(500, 5000), 2),
                assigned_to=random.choice(members),
                location=random.choice(addresses),
                is_active=random.choice([True, True, False]),
                metadata={
                    "warranty_expiry": str(fake.date_between(start_date="today", end_date="+2y")),
                    "supplier": fake.company(),
                    "specs": {
                        "RAM": random.choice(["8GB", "16GB", "32GB"]),
                        "CPU": random.choice(["Intel i5", "Intel i7", "AMD Ryzen 5"]),
                    }
                }
            )
            assets.append(asset)
        return assets

    def process(self):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Assets",
            icon="fa-solid fa-boxes-stacked",
            description="Assets and asset types linked to community members",
            link=reverse("assets:assets_list"),  # ✅ use named URL
            public=False
        )

        # Step 1: asset types
        asset_types = self.create_asset_types()

        if not self.full:
            return

        members = list(CommunityMember.objects.all())
        if len(members) == 0:
            raise ValueError("No Members found to assign as asset holders.")

        # Step 2: assets
        self.create_assets(members, asset_types)

        self.stdout.write(self.style.SUCCESS("✅ Assets data seeded successfully with CommunityMember asset holders."))
