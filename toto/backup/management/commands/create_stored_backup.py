from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from toto.backup.services.stored_backup_service import StoredBackupService
from toto.core.models import Platform


class Command(BaseCommand):
    help = "Create a stored backup ZIP and print its pull URL plus one-time API key."

    def add_arguments(self, parser):
        parser.add_argument("--platform-id", type=int, help="Platform id. Defaults to first Platform.")
        parser.add_argument("--app", action="append", dest="apps", help="App label to include. Repeatable.")
        parser.add_argument("--unsigned", action="store_true", help="Do not sign the backup.")
        parser.add_argument("--expires-days", type=int, default=7, help="Days until pull URL expires. Use 0 for no expiry.")
        parser.add_argument("--base-url", help="Optional base URL to print an absolute pull URL.")

    def handle(self, *args, **options):
        platform = self.get_platform(options.get("platform_id"))
        apps_to_sync = options.get("apps") or list(getattr(settings, "APPS_TO_SYNC", []))
        expires_days = options["expires_days"]
        expires_in = None if expires_days == 0 else timedelta(days=expires_days)

        stored_backup, raw_api_key = StoredBackupService(
            platform=platform,
            apps_to_sync=apps_to_sync,
            sign=not options["unsigned"],
            expires_in=expires_in,
        ).create()

        path = stored_backup.pull_path()
        base_url = (options.get("base_url") or "").rstrip("/")
        pull_url = f"{base_url}{path}" if base_url else path

        self.stdout.write(self.style.SUCCESS("Stored backup created."))
        self.stdout.write(f"UID: {stored_backup.uid}")
        self.stdout.write(f"Pull URL: {pull_url}")
        self.stdout.write(f"API key: {raw_api_key}")
        self.stdout.write("Client header: Authorization: Bearer <API key>")

    def get_platform(self, platform_id):
        if platform_id:
            try:
                return Platform.objects.get(pk=platform_id)
            except Platform.DoesNotExist as exc:
                raise CommandError(f"Platform not found: {platform_id}") from exc
        platform = Platform.objects.order_by("id").first()
        if platform is None:
            raise CommandError("No Platform exists.")
        return platform
