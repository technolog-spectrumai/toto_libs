from toto.core.ingress import IngressCommand
from django.contrib.auth import get_user_model
from faker import Faker
from toto.sketch.models import Board, BoardObject
from toto.vault.models import Bucket

fake = Faker()
User = get_user_model()

BOARD_NAMES = [
    "Project Planning",
    "Design Sketches",
    "Brainstorming Space",
    "Product Roadmap",
    "Sprint Board",
    "Architecture Diagram",
    "User Flow",
    "Wireframes",
    "Mind Map",
    "Retrospective",
    "Stakeholder Map",
    "Feature Breakdown",
]


class Command(IngressCommand):
    help = "Seed sample boards for the admin user"

    def process(self):
        try:
            admin_user = User.objects.get(username="admin")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("User 'admin' not found."))
            return

        bucket = (
            Bucket.objects.filter(owner=admin_user).first()
            or Bucket.objects.create(
                name="Sketch Bucket",
                slug="sketch-bucket",
                owner=admin_user,
            )
        )
        self.stdout.write(self.style.SUCCESS(f"Using bucket: {bucket.name}"))

        for name in BOARD_NAMES:
            board, created = Board.objects.get_or_create(
                name=name,
                owner=admin_user,
                defaults={"id": fake.uuid4()[:12], "bucket": bucket},
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created board: {board.name} ({board.id})"))
                BoardObject.objects.create(
                    board=board,
                    object_id=fake.uuid4(),
                    object_type="placeholder",
                    data={"note": "Sample object"},
                )
            else:
                if not board.bucket:
                    board.bucket = bucket
                    board.save(update_fields=["bucket"])
                    self.stdout.write(self.style.SUCCESS(f"Attached bucket to existing board: {board.name}"))
                else:
                    self.stdout.write(self.style.WARNING(f"Board already exists: {board.name}"))

        self.stdout.write(self.style.SUCCESS("Board seeding complete."))
