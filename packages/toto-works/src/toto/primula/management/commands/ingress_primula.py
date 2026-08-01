"""Seed a couple of sample Primula sheets as ``sheet`` vault files.

Each sheet is a self-contained Univer workbook snapshot (JSON) stored in the vault,
exactly like the app creates them — so ``--full`` gives a fresh platform something to
open in Primula. An initial :class:`~toto.primula.models.SheetVersion` is recorded for
each, matching what a real save does.
"""

import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.primula import sheet_format
from toto.primula.models import SheetVersion
from toto.vault.models import Bucket, VaultDirectory, VaultFile


class Command(IngressCommand):
    help = "Seed sample Primula spreadsheets (sheet vault files) for the admin user."

    def process(self):
        # Demo content only — like the other *full* ingress fixtures.
        if not self.full:
            return

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        try:
            user = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"User '{admin_username}' not found — skipping primula ingress."
            ))
            return

        bucket, _ = Bucket.objects.get_or_create(
            slug="general",
            defaults={"name": "General", "owner": user},
        )
        directory, _ = VaultDirectory.objects.get_or_create(
            name="Sheets", bucket=bucket, parent=None,
            defaults={"owner": user},
        )

        self._seed(user, bucket, directory, "Monthly Budget", "Budget", [
            ["Category", "Planned", "Actual"],
            ["Rent", 1200, 1200],
            ["Groceries", 400, 437],
            ["Transport", 120, 96],
            ["Utilities", 180, 205],
            ["Savings", 500, 500],
            ["Total", 2400, 2438],
        ])
        self._seed(user, bucket, directory, "Project Tracker", "Tasks", [
            ["Task", "Owner", "Status", "Due"],
            ["Design review", "ana", "Done", "2026-08-05"],
            ["Build editor", "you", "In progress", "2026-08-12"],
            ["Offline bundle", "you", "In progress", "2026-08-12"],
            ["Ship", "team", "Todo", "2026-08-20"],
        ])

    def _seed(self, user, bucket, directory, title, sheet_name, rows):
        key = slugify(title)
        if VaultFile.objects.filter(bucket=bucket, key=key).exists():
            self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing sheet: {title}"))
            return

        workbook = sheet_format.workbook_from_rows(title, rows, sheet_name=sheet_name)
        text = sheet_format.dumps(workbook)
        filename = f"{key}.json"

        vault_file = VaultFile(
            owner=user,
            title=title,
            key=key,
            file_type="sheet",
            bucket=bucket,
            directory=directory,
            is_public=True,
            notes="Sample Primula spreadsheet.",
        )
        vault_file.file.save(filename, ContentFile(text.encode("utf-8")), save=False)
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()

        SheetVersion.objects.create(sheet_file=vault_file, snapshot=text, created_by=user, note="seed")

        self.stdout.write(self.style.SUCCESS(
            f"📊 Created sheet '{title}' ({len(rows)} rows)."
        ))
