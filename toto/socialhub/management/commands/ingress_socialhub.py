import random
from django.contrib.auth.models import User
from django.utils import timezone
from toto.core.ingress import IngressCommand
from toto.socialhub.models import Community, Person, EmailService
from toto.locations.models import Address
from toto.gervazy.models import SecretPassword, KeyRing
from django.core.management.base import CommandError
import os


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
        email_service = self.create_email_service()

        self.stdout.write(self.style.NOTICE("🏢 Creating community..."))
        community = self.create_community(
            name="Our Thing Inc.",
            address=address,
            established_year=2024,
            email_service=email_service
        )

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

    def create_community(self, name, address, established_year=None, email_service=None):
        community, created = Community.objects.get_or_create(
            name=name,
            defaults={
                "location": address,
                "established_year": established_year,
                "email_service": email_service
            }
        )
        return community

    # ---------------------------------------------------------
    # Member creation (with admin as founder)
    # ---------------------------------------------------------

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

    def create_email_service(self):
        """
        Creates:
        - SecretPassword (encrypted SMTP password)
        - EmailService (SMTP configuration)
        Using the KeyRing named 'Platform-KeyRing'.
        """

        # -----------------------------------------------------
        # Load SMTP configuration from Django settings
        # -----------------------------------------------------
        email_addr = "noreply@example.com"
        smtp_password = "changeme123"
        smtp_host = "smtp.example.com"
        smtp_port = 587
        smtp_tls = True
        smtp_ssl = False
        pass_phrase_env_var = "EMAIL_PASSPHRASE"
        passphrase = os.environ.get(pass_phrase_env_var, "qwerty")

        if not passphrase:
            raise CommandError("Environment variable EMAIL_PASSPHRASE must be set.")

        # -----------------------------------------------------
        # Find the KeyRing named 'Platform-KeyRing'
        # -----------------------------------------------------
        keyring = KeyRing.objects.filter(name="Platform-KeyRing").first()
        if not keyring:
            raise CommandError(
                "KeyRing named 'Platform-KeyRing' not found. "
                "Create it before running ingress."
            )

        # -----------------------------------------------------
        # Create SecretPassword
        # -----------------------------------------------------
        sp = SecretPassword(
            keyring=keyring,
            name="email-password"
        )
        sp.set_password(smtp_password, passphrase)
        sp.save()

        # -----------------------------------------------------
        # Create EmailService
        # -----------------------------------------------------
        svc = EmailService.objects.create(
            name="default-email-service",
            email_address=email_addr,
            secret_password=sp,
            host=smtp_host,
            port=smtp_port,
            use_tls=smtp_tls,
            use_ssl=smtp_ssl,
            pass_phrase_env_var=pass_phrase_env_var
        )

        self.stdout.write(self.style.SUCCESS(
            f"📨 EmailService created for {email_addr} using encrypted password."
        ))

        return svc