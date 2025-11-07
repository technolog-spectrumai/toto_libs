import random
from django.core.management import call_command
from django.contrib.auth.models import User
from django.utils import timezone
from community.models import Address, Community, CommunityMember
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the platform with fake community data: address, community, members, and relationships"

    def process(self, _):
        self.create_dashboard_item(
            title="Community",
            icon="fa-solid fa-users",
            description="Communities and community members with relationships.",
            link="/community/org-chart/",
            public=False,
        )
        if not self.full:
            return

        self.stdout.write(self.style.NOTICE("📍 Creating community address..."))
        address_args = self.get_address_arguments()
        call_command("create_address", **address_args)
        address_id = self.get_latest_address_id()
        if not address_id:
            self.stderr.write(self.style.ERROR("❌ Address creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🏢 Creating community..."))
        call_command("create_community", "Our Thing Inc.", str(address_id), "--established_year=2024")
        community = self.get_latest_community()
        if not community:
            self.stderr.write(self.style.ERROR("❌ Community creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating community members..."))
        members = self.create_fake_members(community, count=6)

        self.stdout.write(self.style.NOTICE("👑 Assigning head of community..."))
        community.head = members[0]  # Founder is the head
        community.save()

        self.stdout.write(self.style.SUCCESS("✅ Community ingress complete."))

    def get_address_arguments(self):
        return {
            "country_name": "US",
            "state_or_province_name": "California",
            "locality_name": "San Francisco",
            "street": "123 Business St",
            "building": "HQ Tower",
            "apartment": "5A"
        }

    def get_latest_address_id(self):
        latest = Address.objects.order_by("-id").first()
        return latest.id if latest else None

    def get_latest_community(self):
        return Community.objects.order_by("-id").first()

    def create_fake_members(self, community, count=6):
        members = []
        users = []

        for i in range(count):
            username = f"user{i}"
            user, _ = User.objects.get_or_create(username=username, defaults={"email": f"{username}@example.com"})
            users.append(user)

        # Founder
        founder = CommunityMember.objects.create(
            user=users[0],
            display_name="Founder",
            bio="Top-level patron of the community",
            joined_date=timezone.now()
        )
        founder.communities.add(community)
        members.append(founder)

        # Managers
        for i in range(1, 3):
            manager = CommunityMember.objects.create(
                user=users[i],
                display_name=f"Manager {i}",
                bio="Mid-level manager reporting to Founder",
                joined_date=timezone.now(),
                patron=founder
            )
            manager.communities.add(community)
            members.append(manager)

        # Staff
        for i in range(3, count):
            patron = random.choice(members[1:3])
            staff = CommunityMember.objects.create(
                user=users[i],
                display_name=f"Staff {i}",
                bio=f"Team member reporting to {patron.display_name}",
                joined_date=timezone.now(),
                patron=patron
            )
            staff.communities.add(community)
            members.append(staff)

        return members
