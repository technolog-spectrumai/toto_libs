from oya.ingress import IngressCommand
from django.urls import reverse
from faker import Faker
import random

from django.contrib.auth import get_user_model
from chat.models import Room, Message

fake = Faker()
User = get_user_model()


class Command(IngressCommand):
    help = "Seeds demo chat rooms and messages."

    def process(self):

        # ---------------------------------------------------------
        # DASHBOARD ITEM
        # ---------------------------------------------------------
        self.create_dashboard_item(
            title="Chat",
            icon="fa-solid fa-comments",
            description="Real-time chat rooms and messages.",
            link=reverse("chat:chat_home") if self.full else "#",
            public=False,
        )

        if not self.full:
            return

        # ---------------------------------------------------------
        # SAFETY: Prevent duplicate demo data
        # ---------------------------------------------------------
        if Room.objects.filter(name="general").exists():
            print("[Ingress] Chat demo already exists — skipping.")
            return

        # ---------------------------------------------------------
        # REQUIREMENTS: Need at least 1 user
        # ---------------------------------------------------------
        users = list(User.objects.all())

        if not users:
            raise Exception("❌ Need at least 1 User to seed chat.")

        # ---------------------------------------------------------
        # ROOMS
        # ---------------------------------------------------------
        room_general = Room.objects.create(
            name="general",
            created_by=random.choice(users),
        )

        room_random = Room.objects.create(
            name="random",
            created_by=random.choice(users),
        )

        room_private = Room.objects.create(
            name="private",
            created_by=random.choice(users),
        )

        # Assign allowed users to private room
        allowed = random.sample(users, min(3, len(users)))
        room_private.allowed_users.set(allowed)

        # ---------------------------------------------------------
        # MESSAGES
        # ---------------------------------------------------------
        def seed_messages(room, count=10):
            for _ in range(count):
                user = random.choice(users)
                Message.objects.create(
                    room=room,
                    user=user,
                    content=fake.sentence(),
                )

        seed_messages(room_general, 15)
        seed_messages(room_random, 10)
        seed_messages(room_private, 8)

        print("[Ingress] Demo chat registry created successfully.")
