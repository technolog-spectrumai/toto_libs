from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from oya.models import AppBackup
from vault.models import Bucket


class Command(BaseCommand):
    help = "Populate AppBackup entries for all BACKUP_ALLOWED_APPS with a dedicated backup bucket"

    def handle(self, *args, **options):
        created = []
        skipped = []

        # ✅ Ensure a dedicated backup bucket exists
        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).first()
        if not owner:
            self.stdout.write(self.style.ERROR("💥 No superuser found to own the backup bucket"))
            return

        backup_bucket, _ = Bucket.objects.get_or_create(
            name="backup",
            owner=owner,
            defaults={}
        )

        for app_name in AppBackup.BACKUP_ALLOWED_APPS:
            obj, was_created = AppBackup.objects.get_or_create(
                app_name=app_name,
                defaults={
                    "bucket": backup_bucket,
                    "filename": f"{app_name}_backup.json",
                    "scheduled_at": timezone.now()
                }
            )
            if was_created:
                created.append(app_name)
            else:
                skipped.append(app_name)

        if created:
            self.stdout.write(f"✅ Created AppBackup entries for: {', '.join(created)}")
        if skipped:
            self.stdout.write(f"⚠️ Already existed: {', '.join(skipped)}")
        if not created and not skipped:
            self.stdout.write("ℹ️ No apps found in BACKUP_ALLOWED_APPS")

