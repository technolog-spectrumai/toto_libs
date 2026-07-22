import os
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone

from toto.backup.models import StoredBackup

from .backup_engine import BackupEngine
from .backup_service import BackupService


class StoredBackupService:
    """Create a persisted backup ZIP and a one-time-visible API key for pulling it."""

    def __init__(self, *, platform, apps_to_sync=None, sign=True, created_by=None, expires_in=None):
        self.platform = platform
        self.apps_to_sync = apps_to_sync
        self.sign = sign
        self.created_by = created_by
        self.expires_in = expires_in if expires_in is not None else timedelta(days=7)

    def create(self):
        tmp_path = None
        try:
            apps_to_sync = list(self.apps_to_sync or getattr(settings, "APPS_TO_SYNC", []))
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            backup_path = BackupService(
                platform=self.platform,
                apps_to_sync=apps_to_sync,
                sign=self.sign,
            ).create_backup(output_path=tmp_path)

            raw_api_key = None
            ts = timezone.now().strftime("%Y%m%d-%H%M%S")
            backup = StoredBackup(
                platform=self.platform,
                name=f"{self.platform.site_name} backup {ts}",
                apps=apps_to_sync,
                sha256=BackupEngine(self.platform).sha256_file(backup_path),
                size_bytes=Path(backup_path).stat().st_size,
                created_by=self.created_by,
                expires_at=timezone.now() + self.expires_in if self.expires_in else None,
            )
            raw_api_key = backup.issue_api_key()

            with Path(backup_path).open("rb") as fh:
                backup.file.save(Path(backup_path).name, File(fh), save=False)
            backup.save()
            return backup, raw_api_key
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
