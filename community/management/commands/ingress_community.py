import random
from django.contrib.auth.models import User
from django.utils import timezone
from community.models import Community, CommunityMember
from oya.ingress import IngressCommand
from federal.models import Federation
from django.core.exceptions import ObjectDoesNotExist
from locations.models import Address


class Command(IngressCommand):
    help = "Populate the platform with fake community data: address, community, members, and relationships"

    def process(self):
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
        return Address.objects.create(
            country_name="US",
            state_or_province_name="California",
            locality_name="San Francisco",
            street="123 Business St",
            building="HQ Tower",
            apartment="5A"
        )

    @staticmethod
    def get_active_federation() -> Federation:
        try:
            return Federation.objects.get(active=True)
        except ObjectDoesNotExist:
            raise RuntimeError("❌ No active federation found. Please create one before proceeding.")

    def create_community(self, name: str, address: Address, established_year: int = None) -> Community | None:
        federation = self.get_active_federation()
        community, created = Community.objects.get_or_create(
            name=name,
            defaults={
                "location": address,
                "established_year": established_year,
                "federation": federation
            }
        )
        return community

    import random
    from django.utils import timezone
    from django.contrib.auth.models import User
    from community.models import CommunityMember

    def create_fake_members(self, community, count=6):
        members = []

        # 🎲 fetch all existing users
        users = list(User.objects.all())

        if not users:
            self.stderr.write(self.style.ERROR("❌ No users found in the database. Please create some first."))
            return members

        # cap at 12 members max
        max_members = min(count, 12, len(users))

        # randomly select up to max_members users
        selected_users = random.sample(users, max_members)

        # Founder
        founder = CommunityMember.objects.create(
            user=selected_users[0],
            display_name="Founder",
            bio="Top-level patron of the community",
            joined_date=timezone.now(),
        )
        founder.communities.add(community)
        members.append(founder)

        # Managers (next two if available)
        for i, user in enumerate(selected_users[1:3], start=1):
            manager = CommunityMember.objects.create(
                user=user,
                display_name=f"Manager {i}",
                bio="Mid-level manager reporting to Founder",
                joined_date=timezone.now(),
                patron=founder,
            )
            manager.communities.add(community)
            members.append(manager)

        # Staff (remaining users)
        for idx, user in enumerate(selected_users[3:], start=3):
            patron = random.choice(members[1:3]) if len(members) > 2 else founder
            staff = CommunityMember.objects.create(
                user=user,
                display_name=f"Staff {idx}",
                bio=f"Team member reporting to {patron.display_name}",
                joined_date=timezone.now(),
                patron=patron,
            )
            staff.communities.add(community)
            members.append(staff)

        return members

