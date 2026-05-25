from toto.ingress import IngressCommand
from toto.telegraph.models import TelegraphMember, TelegraphChannel
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from toto.people.models import Person

User = get_user_model()


class Command(IngressCommand):
    help = "Seed sample Telegraph channels and member rosters"

    def process(self):

        if not self.full:
            return

        try:
            admin_user = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise Exception("❌ User 'admin' not found. Please create it first.")

        channel_names = [
            "CandyLand",
            "Announcements"
        ]

        admin_person, _ = Person.objects.get_or_create(
            user=admin_user,
            defaults={
                "display_name": admin_user.get_full_name() or admin_user.username,
                "email": admin_user.email,
            },
        )

        for name in channel_names:
            channel, created = TelegraphChannel.objects.get_or_create(
                name=name,
                defaults={
                    "slug": slugify(name),
                    "created_by": admin_user,
                }
            )

            TelegraphMember.objects.get_or_create(
                channel=channel,
                person=admin_person,
                defaults={"is_active": True},
            )
            channel.participants.add(admin_user)

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"💬 Created channel: {channel.name} with admin on the member roster"
                    )
                )
            else:
                self.stdout.write(self.style.WARNING(f"⚠️ Channel already exists: {channel.name}"))

        self.stdout.write(self.style.SUCCESS("✅ Telegraph channel seeding complete."))
