# forum/management/commands/ingress.py
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from forum.models import Room
from oya.ingress import IngressCommand
import random
import string

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

        for i in range(3):
            room = Room.objects.create(
                name=f"Demo Room {i+1}",
                max_bytes=random.randint(1500, 3000)
            )

            self.stdout.write(f"Created {room.name} with max_bytes={room.max_bytes}")

            # Create root messages and replies
            for j in range(10):
                root_content = self._random_text(size=random.randint(100, 300))
                root_msg = room.post(root_content)

                # Add replies
                for k in range(random.randint(1, 3)):
                    reply_content = self._random_text(size=random.randint(50, 150))
                    room.post(reply_content, parent=root_msg)

        self.stdout.write(self.style.SUCCESS("Demo forum setup complete."))

    def _random_text(self, size=100):
        return ''.join(random.choices(string.ascii_letters + string.digits + ' ', k=size))
