import random
from django.contrib.auth.models import User
from django.utils import timezone
from community.models import Address, Community, CommunityMember, SocialEntity
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the platform with fake community data: address, community, members, and relationships"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Community",
            icon="fa-solid fa-users",
            description="Communities and community members with relationships.",
            link="/community/org-chart/",
            public=False
        )
        if not self.full:
            return

        self.stdout.write(self.style.NOTICE("📍 Creating community address..."))
        address = self.create_address()
        if not address:
            self.stderr.write(self.style.ERROR("❌ Address creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🏢 Creating community..."))
        community = self.create_community("Our Thing Inc.", address, established_year=2024)
        if not community:
            self.stderr.write(self.style.ERROR("❌ Community creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating community members..."))
        members = self.create_fake_members(community, count=6)

        self.stdout.write(self.style.NOTICE("👑 Assigning head of community..."))
        community.head = members[0]
        community.save()

        self.stdout.write(self.style.SUCCESS("✅ Community ingress complete."))

    def create_address(self):
        address = Address.objects.create(
            country_name="US",
            state_or_province_name="California",
            locality_name="San Francisco",
            street="123 Business St",
            building="HQ Tower",
            apartment="5A"
        )
        return address

    def create_community(self, name: str, address: Address, established_year: int = None) -> Community | None:
        social_entity = SocialEntity.objects.create(name=name)
        community, created = Community.objects.get_or_create(
            name=name,
            defaults={
                "address": address,
                "established_year": established_year,
                "social_entity": social_entity
            }
        )
        if not created and not community.social_entity:
            community.social_entity = social_entity
            community.save()

        return community

    def create_fake_members(self, community, count=6):
        members = []
        users = []

        for i in range(count):
            username = f"user{i}"
            user, _ = User.objects.get_or_create(username=username, defaults={"email": f"{username}@example.com"})
            users.append(user)

        # Founder
        founder_entity = SocialEntity.objects.create(name="Founder")
        founder = CommunityMember.objects.create(
            user=users[0],
            display_name="Founder",
            bio="Top-level patron of the community",
            joined_date=timezone.now(),
            social_entity=founder_entity
        )
        founder.communities.add(community)
        members.append(founder)

        # Managers
        for i in range(1, 3):
            manager_entity = SocialEntity.objects.create(name=f"Manager {i}")
            manager = CommunityMember.objects.create(
                user=users[i],
                display_name=f"Manager {i}",
                bio="Mid-level manager reporting to Founder",
                joined_date=timezone.now(),
                patron=founder,
                social_entity=manager_entity
            )
            manager.communities.add(community)
            members.append(manager)

        # Staff
        for i in range(3, count):
            patron = random.choice(members[1:3])
            staff_entity = SocialEntity.objects.create(name=f"Staff {i}")
            staff = CommunityMember.objects.create(
                user=users[i],
                display_name=f"Staff {i}",
                bio=f"Team member reporting to {patron.display_name}",
                joined_date=timezone.now(),
                patron=patron,
                social_entity=staff_entity
            )
            staff.communities.add(community)
            members.append(staff)

        return members
