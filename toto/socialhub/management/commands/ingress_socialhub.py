import random
from django.contrib.auth.models import User
from django.utils import timezone
from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.locations.models import Address


class Command(IngressCommand):
    help = "Populate the platform with fake community data: address, community, members, and relationships"

    # ---------------------------------------------------------
    # Main process
    # ---------------------------------------------------------

    def ensure_fake_users(self, count=10):
        """
        Creates fake users if the database has none.
        """
        existing = User.objects.count()
        if existing >= count:
            self.stdout.write(self.style.SUCCESS(f"✔ Found {existing} users. No need to create fake users."))
            return

        needed = count - existing
        self.stdout.write(self.style.WARNING(f"⚠ Only {existing} users found. Creating {needed} fake users..."))

        for i in range(needed):
            username = f"user{i + 1}"
            email = f"user{i + 1}@example.com"

            User.objects.create_user(
                username=username,
                email=email,
                password="password123"
            )

        self.stdout.write(self.style.SUCCESS(f"✔ Created {needed} fake users."))

    def process(self):

        if not self.full:
            return

        self.stdout.write(self.style.NOTICE("👤 Checking for existing users..."))
        self.ensure_fake_users(count=7)

        self.stdout.write(self.style.NOTICE("📍 Creating address..."))
        address = Address.objects.first()

        self.stdout.write(self.style.NOTICE("🏢 Creating community..."))
        community = self.create_community(
            name="Our Thing Inc.",
            address=address,
            established_year=2024,
        )
        tester_user, tester_person = self.ensure_tester_person(community=community)
        self.stdout.write(self.style.NOTICE(f"Created tester: {tester_person}"))

        self.stdout.write(self.style.NOTICE("🧑‍🤝‍🧑 Creating members..."))
        members = self.create_members(community, count=6)

        if members:
            self.stdout.write(self.style.NOTICE("👑 Assigning head..."))
            community.head = members[0]
            community.save()

        self.stdout.write(self.style.SUCCESS("✅ SocialHub ingress complete."))

    # ---------------------------------------------------------
    # Community creation
    # ---------------------------------------------------------

    def create_community(self, name, address, established_year=None):
        community, created = Community.objects.get_or_create(
            name=name,
            defaults={
                "location": address,
                "established_year": established_year,
            }
        )
        return community

    # ---------------------------------------------------------
    # Member creation (with admin as founder)
    # ---------------------------------------------------------

    def ensure_tester_person(self, community=None):
        """
        Creates or reuses a special Django user named `tester`
        and ensures they have a Person profile.
        """
        tester_user, user_created = User.objects.get_or_create(
            username="tester",
            defaults={
                "email": "tester@example.com",
                "is_staff": False,
                "is_superuser": False,
            }
        )

        if user_created:
            tester_user.set_password("tester")
            tester_user.save()

            self.stdout.write(
                self.style.SUCCESS("✔ Created special tester user.")
            )
        else:
            self.stdout.write(
                self.style.WARNING("⚠ Tester user already exists.")
            )

        tester_person, person_created = Person.objects.get_or_create(
            user=tester_user,
            defaults={
                "display_name": "Tester",
                "bio": "Special testing user for the platform.",
                "joined_date": timezone.now(),
                "patron": None,
            }
        )

        if community:
            tester_person.communities.add(community)

        if person_created:
            self.stdout.write(
                self.style.SUCCESS("✔ Created Person profile for tester.")
            )
        else:
            self.stdout.write(
                self.style.WARNING("⚠ Tester Person profile already exists.")
            )

        return tester_user, tester_person

    def create_members(self, community, count=6):
        """
        Creates a founder (admin if available), managers, and staff members.
        Ensures no duplicate Person is created for a user.
        """
        members = []

        all_users = list(User.objects.all())
        if not all_users:
            self.stderr.write(self.style.ERROR("❌ No users found. Create some users first."))
            return members

        # Avoid selecting users already linked to a Person
        used_user_ids = set(Person.objects.values_list("user_id", flat=True))
        available_users = [u for u in all_users if u.id not in used_user_ids]

        if not available_users:
            self.stderr.write(self.style.ERROR("❌ All users already have community profiles."))
            return members

        # Prefer admin as founder
        admin_user = User.objects.filter(username="admin").first()
        if admin_user and admin_user in available_users:
            founder_user = admin_user
            available_users.remove(admin_user)
        else:
            founder_user = available_users.pop(0)

        # Founder
        founder = self.create_member(
            user=founder_user,
            display_name="Founder",
            bio="Top-level patron of the community",
            community=community,
            patron=None
        )
        members.append(founder)

        # Managers (2)
        manager_candidates = available_users[:2]
        for i, user in enumerate(manager_candidates, start=1):
            manager = self.create_member(
                user=user,
                display_name=f"Manager {i}",
                bio="Mid-level manager reporting to Founder",
                community=community,
                patron=founder
            )
            members.append(manager)

        # Staff
        staff_candidates = available_users[2:]
        for idx, user in enumerate(staff_candidates, start=3):
            patron = random.choice(members[1:3]) if len(members) > 2 else founder
            staff = self.create_member(
                user=user,
                display_name=f"Staff {idx}",
                bio=f"Team member reporting to {patron.display_name}",
                community=community,
                patron=patron
            )
            members.append(staff)

        return members

    # ---------------------------------------------------------
    # Helper: create a single member
    # ---------------------------------------------------------

    def create_member(self, user, display_name, bio, community, patron=None):
        member = Person.objects.create(
            user=user,
            display_name=display_name,
            bio=bio,
            joined_date=timezone.now(),
            patron=patron
        )
        member.communities.add(community)
        return member
