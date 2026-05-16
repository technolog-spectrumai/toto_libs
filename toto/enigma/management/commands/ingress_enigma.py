from toto.ingress import IngressCommand
from toto.enigma.models import Participant, Room
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from toto.socialhub.models import Person

User = get_user_model()


class Command(IngressCommand):
    help = "Seed sample Enigma chat rooms and participant rosters"

    def process(self):

        if not self.full:
            return

        # 🔍 Find admin user
        try:
            admin_user = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise Exception("❌ User 'admin' not found. Please create it first.")

        room_names = [
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

        for name in room_names:
            room, created = Room.objects.get_or_create(
                name=name,
                defaults={
                    "slug": slugify(name),
                    "created_by": admin_user,
                }
            )

            Participant.objects.get_or_create(
                room=room,
                person=admin_person,
                defaults={"is_active": True},
            )
            room.participants.add(admin_user)

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"💬 Created room: {room.name} with admin on the participant roster"
                    )
                )
            else:
                self.stdout.write(self.style.WARNING(f"⚠️ Room already exists: {room.name}"))

        self.stdout.write(self.style.SUCCESS("✅ Chat room seeding complete."))
