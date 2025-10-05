from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from forum.models import Room
from oya.ingress import IngressCommand
import random
import lorem

class Command(IngressCommand):
    help = "Creates a demo forum setup with rooms and byte-limited message buffers"

    def process(self, _):
        self.create_dashboard_item(
            title="Forum",
            icon="comments",
            description="A forum with rooms and circular byte buffers for messages.",
            link="/forum/"
        )

        self.stdout.write("Creating demo rooms and messages...")

        users = self._get_or_create_demo_users()

        for i in range(3):
            room = Room.objects.create(
                name=f"Demo Room {i+1}",
                max_bytes=random.randint(1500, 3000)
            )

            self.stdout.write(f"Created {room.name} with max_bytes={room.max_bytes}")

            # Create root messages and replies
            for j in range(10):
                author = random.choice(users)
                root_content = self._random_lorem(size=random.randint(100, 300))
                root_msg = room.post(root_content, user=author)

                if root_msg:
                    for k in range(random.randint(1, 3)):
                        reply_author = random.choice(users)
                        reply_content = self._random_lorem(size=random.randint(50, 150))
                        room.post(reply_content, parent=root_msg, user=reply_author)

        self.stdout.write(self.style.SUCCESS("Demo forum setup complete."))

    def _get_or_create_demo_users(self):
        usernames = ['alice', 'bob', 'carol', 'dave']
        users = []
        for username in usernames:
            user, _ = User.objects.get_or_create(username=username, defaults={
                'email': f'{username}@example.com',
                'password': 'demo'  # Not secure, just for demo
            })
            users.append(user)
        return users

    def _random_lorem(self, size=100):
        text = lorem.paragraph()
        while len(text) < size:
            text += ' ' + lorem.sentence()
        return text[:size]
