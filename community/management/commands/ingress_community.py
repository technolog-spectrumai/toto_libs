import random
from django.core.management import call_command
from django.contrib.auth.models import User
from django.utils import timezone
from community.models import Address, Company, Branch, CommunityMember
from oya.ingress import IngressCommand

class Command(IngressCommand):
    help = "Populate the platform with fake community data: address, company, branches, members, and relationships"

    def process(self, _):
        self.create_dashboard_item(
            title="Community",
            icon="fa-solid fa-users",
            description="Company, branches, and community members with relationships.",
            link="/community/org-chart/",
            public=False,
        )

        self.stdout.write(self.style.NOTICE("📍 Creating company address..."))
        address_args = self.get_address_arguments()
        call_command("create_address", **address_args)
        address_id = self.get_latest_address_id()
        if not address_id:
            self.stderr.write(self.style.ERROR("❌ Address creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🏢 Creating company..."))
        call_command("create_company", "Our Thing Inc.", str(address_id), "--established_year=2024")
        company = self.get_latest_company()
        if not company:
            self.stderr.write(self.style.ERROR("❌ Company creation failed."))
            return

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating community members..."))
        members = self.create_fake_members(count=6)

        self.stdout.write(self.style.NOTICE("👑 Assigning head of company..."))
        company.head = members[0]  # Founder is the head
        company.save()

        self.stdout.write(self.style.NOTICE("🏬 Creating branches..."))
        branches = self.create_fake_branches(company, members)

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

    def get_latest_company(self):
        return Company.objects.order_by("-id").first()

    def create_fake_members(self, count=6):
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
            members.append(staff)

        return members

    def create_fake_branches(self, company, members):
        branches = []
        senior_members = members[:3]  # Founder + Managers

        for i in range(3):
            address = Address.objects.create(
                country_name="US",
                state_or_province_name="California",
                locality_name=f"Branch City {i}",
                street=f"{100+i} Branch Ave",
                building=f"B{i}",
                apartment=None
            )
            branch = Branch.objects.create(
                company=company,
                name=f"Branch {i}",
                address=address,
                head=senior_members[i % len(senior_members)]
            )

            assigned = random.sample(members, k=3)
            for member in assigned:
                member.membership.add(branch)

            branches.append(branch)

        return branches