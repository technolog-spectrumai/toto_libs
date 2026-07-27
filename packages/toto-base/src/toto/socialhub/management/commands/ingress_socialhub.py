import os
import random
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community, CommunityNewsPost, CommunityNewsTopic, Constitution
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

        self.ensure_default_community()

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

        self.assign_senior_members(community, tester_person, members)
        self.create_community_news(community, tester_person)
        self.create_constitution(community)

        self.stdout.write(self.style.NOTICE("🏘 Creating child communities..."))
        child_a = self.create_community(
            name="Our Thing North Chapter",
            address=address,
            established_year=2025,
        )
        child_a.parent = community
        if len(members) > 1:
            child_a.head = members[1]
            members[1].communities.add(child_a)
        child_a.save()
        self.stdout.write(self.style.SUCCESS(f"✔ Created child community: {child_a.name}"))

        child_b = self.create_community(
            name="Our Thing South Chapter",
            address=address,
            established_year=2025,
        )
        child_b.parent = community
        if len(members) > 2:
            child_b.head = members[2]
            members[2].communities.add(child_b)
        child_b.save()
        self.stdout.write(self.style.SUCCESS(f"✔ Created child community: {child_b.name}"))

        self.stdout.write(self.style.SUCCESS("✅ SocialHub ingress complete."))

    # ---------------------------------------------------------
    # Baseline community (all ingress modes)
    # ---------------------------------------------------------

    def ensure_default_community(self):
        """
        Seed the deployment's real community even without FULL_INGRESS: create
        the community named by the DEFAULT_COMMUNITY env var (deploy YAML `env:`
        block) and make the admin person (created by init_data from
        ADMIN_DISPLAY_NAME, e.g. "Founder") a member of it. No-op when
        DEFAULT_COMMUNITY is unset.
        """
        name = os.environ.get("DEFAULT_COMMUNITY", "").strip()
        if not name:
            return

        community, created = Community.objects.get_or_create(name=name)
        if created:
            self.stdout.write(self.style.SUCCESS(f"✔ Created default community: {name}"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ Default community already exists: {name}"))

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_person = Person.objects.filter(user__username=admin_username).first()
        if admin_person is None:
            self.stdout.write(self.style.WARNING(
                f"⚠ No person profile for '{admin_username}' — nobody added to '{name}'."
            ))
            return

        admin_person.communities.add(community)
        self.stdout.write(self.style.SUCCESS(
            f"✔ Added '{admin_person.display_name}' to '{name}'."
        ))

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

    # Rich fake-person data for realistic seeding
    FAKE_PEOPLE = [
        {"name": "Eleanor Voss",     "email": "e.voss@example.com",      "phone": "+1-202-555-0101", "bio": "Founder and strategic lead. Oversees all departments."},
        {"name": "Marcus Hale",      "email": "m.hale@example.com",       "phone": "+1-202-555-0102", "bio": "Operations manager. Coordinates logistics and team workflows."},
        {"name": "Sofia Reyes",      "email": "s.reyes@example.com",      "phone": "+1-202-555-0103", "bio": "Head of communications. Manages external relations and media."},
        {"name": "Daniel Okoye",     "email": "d.okoye@example.com",      "phone": "+1-202-555-0104", "bio": "Infrastructure lead. Responsible for technical systems."},
        {"name": "Priya Nair",       "email": "p.nair@example.com",       "phone": "+1-202-555-0105", "bio": "Finance coordinator. Tracks budgets and member contributions."},
        {"name": "James Whitfield",  "email": "j.whitfield@example.com",  "phone": "+1-202-555-0106", "bio": "Community outreach. Builds partnerships and recruits new members."},
        {"name": "Amara Diallo",     "email": "a.diallo@example.com",     "phone": "+1-202-555-0107", "bio": "Events organiser. Plans and runs community gatherings."},
    ]

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

        used_user_ids = set(Person.objects.values_list("user_id", flat=True))
        available_users = [u for u in all_users if u.id not in used_user_ids]

        if not available_users:
            self.stderr.write(self.style.ERROR("❌ All users already have community profiles."))
            return members

        admin_user = User.objects.filter(username="admin").first()
        if admin_user and admin_user in available_users:
            founder_user = admin_user
            available_users.remove(admin_user)
        else:
            founder_user = available_users.pop(0)

        fake = self.FAKE_PEOPLE
        address = Address.objects.first()

        # Founder
        founder = self.create_member(
            user=founder_user,
            display_name=fake[0]["name"],
            bio=fake[0]["bio"],
            email=fake[0]["email"],
            phone=fake[0]["phone"],
            address=address,
            community=community,
            patron=None,
        )
        members.append(founder)

        # Managers (2)
        for i, user in enumerate(available_users[:2], start=1):
            info = fake[i] if i < len(fake) else {"name": f"Manager {i}", "bio": "", "email": "", "phone": ""}
            manager = self.create_member(
                user=user,
                display_name=info["name"],
                bio=info["bio"],
                email=info["email"],
                phone=info["phone"],
                address=address,
                community=community,
                patron=founder,
            )
            members.append(manager)

        # Staff
        for idx, user in enumerate(available_users[2:], start=3):
            patron = random.choice(members[1:3]) if len(members) > 2 else founder
            info = fake[idx] if idx < len(fake) else {"name": f"Staff {idx}", "bio": "", "email": "", "phone": ""}
            staff = self.create_member(
                user=user,
                display_name=info["name"],
                bio=info["bio"],
                email=info["email"],
                phone=info["phone"],
                address=address,
                community=community,
                patron=patron,
            )
            members.append(staff)

        return members

    # ---------------------------------------------------------
    # Helper: create a single member
    # ---------------------------------------------------------

    def create_member(self, user, display_name, bio, community, patron=None,
                      email=None, phone=None, address=None):
        member = Person.objects.create(
            user=user,
            display_name=display_name,
            bio=bio,
            email=email,
            phone=phone,
            address=address,
            joined_date=timezone.now(),
            patron=patron,
        )
        member.communities.add(community)
        return member

    def assign_senior_members(self, community, tester_person, members):
        senior_members = [tester_person, *members[:2]]
        community.senior_members.add(*[member for member in senior_members if member])
        self.stdout.write(self.style.SUCCESS("✔ Assigned senior community members."))

    def create_community_news(self, community, author):
        topics = self.create_news_topics()

        samples = [
            {
                "title": "Friday notes are open",
                "content": "<div>Drop short updates, blockers, and tiny wins here. Longer reflections can still move into Palimpsest.</div>",
                "topics": ["announcements", "community"],
            },
            {
                "title": "",
                "content": "<div>Small reminder: a good update is often just one useful sentence plus a next step.</div>",
                "topics": ["microblog", "craft"],
            },
            {
                "title": "Community publishing rhythm",
                "content": "<div>News is for the pulse. Palimpsest is for the piece.</div>",
                "topics": ["announcements", "craft"],
            },
        ]

        for data in samples:
            post, created = CommunityNewsPost.objects.get_or_create(
                community=community,
                title=data["title"],
                content=data["content"],
                defaults={
                    "author": author,
                },
            )
            if created:
                post.topics.set([topics[name] for name in data["topics"]])
                self.stdout.write(self.style.SUCCESS(f"✔ Created community news: {post.display_title}"))

    def create_constitution(self, community):
        constitution, created = Constitution.objects.get_or_create(
            community=community,
            is_active=True,
            defaults={
                "title": f"{community.name} Constitution",
                "version": "I",
                "body": (
                    f"Constitution of {community.name}\n"
                    "Version I\n\n"
                    "Article 1 — Purpose\n"
                    f"{community.name} exists to advance the shared interests of its members through "
                    "cooperation, transparency, and mutual accountability.\n\n"
                    "Article 2 — Membership\n"
                    "Membership is open to any person accepted through the standard application process. "
                    "Members are expected to act in good faith and uphold the community's values.\n\n"
                    "Article 3 — Governance\n"
                    "Community governance is carried out through the Assembly. Executive decisions may be "
                    "delegated to appointed Magistrates within the bounds set by the Assembly.\n\n"
                    "Article 4 — Finances\n"
                    "All financial decisions affecting the community treasury require Assembly ratification. "
                    "Magistrates may issue Finance Directives as defined by community policy.\n\n"
                    "Article 5 — Amendments\n"
                    "This constitution may be amended by a supermajority vote of the Assembly. "
                    "Amendments take effect upon publication of a new active version."
                ),
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"✔ Created constitution for {community.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ Constitution for {community.name} already exists"))

    def create_news_topics(self):
        names = ["announcements", "community", "microblog", "craft"]
        topics = {}
        for name in names:
            topic, _ = CommunityNewsTopic.objects.get_or_create(
                slug=slugify(name),
                defaults={"name": name.replace("-", " ").title()},
            )
            topics[name] = topic
        return topics
