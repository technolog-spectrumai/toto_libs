from toto.ingress import IngressCommand
from django.contrib.auth import get_user_model
from toto.vault.models import Bucket
from toto.ocr.models import OcrProject

User = get_user_model()

PROJECT_NAMES = [
    "Document Digitization",
    "Invoice Processing",
    "Handwritten Notes",
    "Archive Scanning",
    "Receipt Extraction",
    "Contract Analysis",
    "Form Recognition",
    "Historical Records",
]


class Command(IngressCommand):
    help = "Seed sample OCR projects for the admin user"

    def process(self):
        if not self.full:
            return

        try:
            admin_user = User.objects.get(username="admin")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("User 'admin' not found."))
            return

        bucket, bucket_created = Bucket.objects.get_or_create(
            name="OCR Bucket",
            owner=admin_user,
            defaults={"slug": "ocr-bucket"},
        )
        if bucket_created:
            self.stdout.write(self.style.SUCCESS("Created bucket: OCR Bucket"))
        else:
            self.stdout.write(self.style.WARNING("Bucket already exists: OCR Bucket"))

        for name in PROJECT_NAMES:
            slug = name.lower().replace(" ", "-")
            project, created = OcrProject.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "bucket": bucket},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created project: {name}"))
            else:
                self.stdout.write(self.style.WARNING(f"Project already exists: {name}"))

        self.stdout.write(self.style.SUCCESS("OCR seeding complete."))
