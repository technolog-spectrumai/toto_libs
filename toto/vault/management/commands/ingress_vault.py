from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.core.files.base import ContentFile
from django.utils.text import slugify
from toto.ingress import IngressCommand
from toto.vault.models import VaultFile, Bucket, BucketBilling, StorageTokenPrice, FileGateway, VaultDirectory


class Command(IngressCommand):
    help = "Seed Vault demo data including buckets, directories, files, and upload gateways"

    def process(self):
        from toto.tariffs.models import BillingUnit

        storage_units = [
            {"code": "storage.byte",    "label": "Storage byte",      "dimension": "storage"},
            {"code": "storage.kb",      "label": "Storage kilobyte",  "dimension": "storage"},
            {"code": "storage.mb",      "label": "Storage megabyte",  "dimension": "storage"},
            {"code": "storage.gb",      "label": "Storage gigabyte",  "dimension": "storage"},
            {"code": "storage.mb_hour", "label": "Megabyte hour",     "dimension": "storage_time"},
            {"code": "storage.gb_hour", "label": "Gigabyte hour",     "dimension": "storage_time"},
        ]

        created = updated = unchanged = 0
        for unit in storage_units:
            obj, was_created = BillingUnit.objects.get_or_create(
                code=unit["code"],
                defaults={
                    "label": unit["label"],
                    "dimension": unit["dimension"],
                    "app_label": "vault",
                    "active": True,
                    "metadata": {},
                },
            )
            if was_created:
                created += 1
                continue

            changed = False
            for field in ("label", "dimension"):
                if getattr(obj, field) != unit[field]:
                    setattr(obj, field, unit[field])
                    changed = True
            if obj.app_label != "vault":
                obj.app_label = "vault"
                changed = True
            if obj.active is not True:
                obj.active = True
                changed = True

            if changed:
                obj.save(update_fields=["label", "dimension", "app_label", "active"])
                updated += 1
            else:
                unchanged += 1

        self.stdout.write(self.style.SUCCESS(
            f"Vault storage billing units ready: {created} created, "
            f"{updated} updated, {unchanged} unchanged."
        ))

        if not self.full:
            return

        # ── Users ────────────────────────────────────────────────────────
        try:
            user = User.objects.get(username="admin")
            self.stdout.write(f"Found demo user: {user.username}")
        except User.DoesNotExist:
            raise CommandError("Demo user 'admin' not found. Please create the user first.")

        users = list(User.objects.all())
        if not users:
            raise CommandError("No users found. Create at least one user first.")

        # ── Buckets ──────────────────────────────────────────────────────
        bucket_legal, _ = Bucket.objects.get_or_create(
            name="Legal",
            defaults={"owner": user, "slug": "legal"},
        )
        bucket_finance, _ = Bucket.objects.get_or_create(
            name="Finance",
            defaults={"owner": user, "slug": "finance"},
        )
        bucket_media, _ = Bucket.objects.get_or_create(
            name="Media",
            defaults={"owner": user, "slug": "media"},
        )
        self.stdout.write(self.style.SUCCESS("Buckets ready."))

        # ── BucketBilling + token pricing for Finance bucket ─────────────
        self._seed_finance_billing(bucket_finance)

        # ── Directory tree ───────────────────────────────────────────────
        #
        # Legal/
        #   ├── Contracts/
        #   │     ├── Vendors/
        #   │     └── Clients/
        #   └── Compliance/
        #
        # Finance/
        #   ├── Reports/
        #   │     ├── Q1 2026/
        #   │     └── Q2 2026/
        #   └── Invoices/
        #
        # Media/
        #   ├── Logos/
        #   └── Archive/

        def mkdir(name, bucket, parent=None, restricted=False):
            d, created = VaultDirectory.objects.get_or_create(
                name=name, bucket=bucket, parent=parent,
                defaults={"owner": user},
            )
            if restricted and not d.allowed_users.exists():
                d.allowed_users.set([user])
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + dir {d.full_path()}"))
            return d

        # Legal tree
        contracts    = mkdir("Contracts",  bucket_legal)
        mkdir("Vendors",    bucket_legal, parent=contracts)
        mkdir("Clients",    bucket_legal, parent=contracts)
        compliance   = mkdir("Compliance", bucket_legal, restricted=True)

        # Finance tree
        reports      = mkdir("Reports",    bucket_finance)
        mkdir("Q1 2026",    bucket_finance, parent=reports)
        mkdir("Q2 2026",    bucket_finance, parent=reports)
        mkdir("Invoices",   bucket_finance, restricted=True)

        # Media tree
        mkdir("Logos",      bucket_media)
        mkdir("Archive",    bucket_media)

        self.stdout.write(self.style.SUCCESS("Directory tree ready."))

        # ── Demo files ───────────────────────────────────────────────────
        #
        # We create small placeholder text files so no real media storage
        # is required for seeding. Adjust file content / upload_to as needed.

        def mkfile(title, bucket, directory, file_type="text", notes=""):
            slug_key = slugify(title)
            if VaultFile.objects.filter(bucket=bucket, key=slug_key).exists():
                self.stdout.write(f"  ~ file already exists: {title}")
                return
            content = f"Demo content for {title}\n".encode()
            vf = VaultFile(
                owner=user,
                title=title,
                key=slug_key,
                file_type=file_type,
                bucket=bucket,
                directory=directory,
                is_public=True,
                notes=notes,
            )
            vf.file.save(f"{slug_key}.txt", ContentFile(content), save=False)
            vf.save()
            self.stdout.write(self.style.SUCCESS(f"  + file {title}"))

        # Legal / Contracts
        contracts_dir = VaultDirectory.objects.get(name="Contracts", bucket=bucket_legal, parent=None)
        vendors_dir   = VaultDirectory.objects.get(name="Vendors",   bucket=bucket_legal, parent=contracts_dir)
        clients_dir   = VaultDirectory.objects.get(name="Clients",   bucket=bucket_legal, parent=contracts_dir)

        mkfile("Master Service Agreement",  bucket_legal, contracts_dir, "pdf",  "Signed 2025-01")
        mkfile("Vendor NDA - Acme Corp",    bucket_legal, vendors_dir,   "pdf",  "Confidential")
        mkfile("Vendor NDA - BuildRight",   bucket_legal, vendors_dir,   "pdf",  "Confidential")
        mkfile("Client Contract - Stark",   bucket_legal, clients_dir,   "pdf",  "Active")
        mkfile("Client Contract - Wayne",   bucket_legal, clients_dir,   "pdf",  "Active")

        # Legal / Compliance (restricted)
        mkfile("GDPR Assessment 2025",      bucket_legal, compliance,    "pdf",  "Internal only")
        mkfile("Audit Report Q4 2025",      bucket_legal, compliance,    "pdf",  "Restricted")

        # Finance / Reports
        q1 = VaultDirectory.objects.get(name="Q1 2026", bucket=bucket_finance)
        q2 = VaultDirectory.objects.get(name="Q2 2026", bucket=bucket_finance)

        mkfile("Revenue Summary Q1 2026",   bucket_finance, q1,  "pdf",  "Board approved")
        mkfile("Cost Breakdown Q1 2026",    bucket_finance, q1,  "json", "Exported from ERP")
        mkfile("Revenue Summary Q2 2026",   bucket_finance, q2,  "pdf",  "Draft")

        # Finance / Invoices (restricted)
        invoices_dir = VaultDirectory.objects.get(name="Invoices", bucket=bucket_finance)
        mkfile("Invoice INV-2026-001",      bucket_finance, invoices_dir, "pdf", "Paid")
        mkfile("Invoice INV-2026-002",      bucket_finance, invoices_dir, "pdf", "Pending")

        # Media
        logos_dir   = VaultDirectory.objects.get(name="Logos",   bucket=bucket_media)
        archive_dir = VaultDirectory.objects.get(name="Archive",  bucket=bucket_media)

        mkfile("Logo Primary",              bucket_media, logos_dir,   "svg",  "Primary brand mark")
        mkfile("Logo Mono",                 bucket_media, logos_dir,   "svg",  "Single-colour variant")
        mkfile("Brand Guidelines 2024",     bucket_media, archive_dir, "pdf",  "Superseded by 2025 version")

        self.stdout.write(self.style.SUCCESS("Demo files seeded."))

        # ── Gateways (one per directory, no root uploads) ────────────────
        #
        # Seed three specific directories with upload gateways so that demo
        # data reflects the 1-to-1 directory→gateway constraint.

        gateway_dirs = [
            (contracts_dir,  "Legal Contracts Upload",   "Submit new vendor or client contracts here."),
            (reports,        "Finance Reports Upload",    "Submit quarterly financial reports here."),
            (logos_dir,      "Media Logos Upload",        "Upload brand logo assets here."),
        ]

        for directory, name, description in gateway_dirs:
            gateway, created = FileGateway.objects.get_or_create(
                directory=directory,
                defaults={
                    "name": name,
                    "description": description,
                    "make_public": True,
                },
            )
            gateway.allowed_users.set([user])
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created gateway: {name} → {directory.full_path()}"))
            else:
                self.stdout.write(f"Gateway already exists: {name}")

        self.stdout.write(self.style.SUCCESS("All vault demo data seeded."))

    def _seed_finance_billing(self, bucket_finance):
        """
        Create a BucketBilling for the Finance bucket with reasonable demo
        pricing so the Buy Tokens page works out of the box:

          TUSD  → 0.01 TUSD  per STORAGE_TOKEN  (1 cent each, 100 tokens = $1)
          TPLN  → 0.04 TPLN  per STORAGE_TOKEN  (~4 groszy each, ~PLN/USD 4:1)

        Uses REV-STORAGE as the revenue account (created by ingress_tariffs).
        Falls back gracefully if any asset or account is missing.
        """
        from toto.assets.models import Asset, LedgerAccount, AccountType

        # Storage token asset
        storage_asset = Asset.objects.filter(unit_name="STORAGE_TOKEN", active=True).first()
        if not storage_asset:
            self.stdout.write(self.style.WARNING(
                "  ⚠  STORAGE_TOKEN asset not found — skipping BucketBilling seed. "
                "Run ingress_tariffs first."
            ))
            return

        # Revenue account — prefer REV-STORAGE created by ingress_tariffs
        revenue_account, rev_created = LedgerAccount.objects.get_or_create(
            code="REV-STORAGE",
            defaults={
                "name": "Storage Revenue",
                "account_type": AccountType.SYSTEM,
                "active": True,
            },
        )
        if rev_created:
            self.stdout.write("  + created REV-STORAGE account")

        # BucketBilling (idempotent)
        billing, created = BucketBilling.objects.get_or_create(
            bucket=bucket_finance,
            defaults={
                "storage_quota_mb": 500,   # 500 MB per user for demo
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(
                f"  + BucketBilling for '{bucket_finance.name}' (quota 500 MB)"
            ))
        else:
            self.stdout.write(f"  ~ BucketBilling for '{bucket_finance.name}' already exists")

        # Pricing table: (unit_name, price_per_token, human note)
        price_schedule = [
            ("TUSD", Decimal("0.01"),  "1 cent per token"),
            ("TPLN", Decimal("0.04"),  "4 groszy per token"),
        ]

        for unit_name, price, note in price_schedule:
            asset = Asset.objects.filter(unit_name=unit_name, active=True).first()
            if not asset:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠  {unit_name} asset not found — skipping price entry."
                ))
                continue

            tp, tp_created = StorageTokenPrice.objects.get_or_create(
                bucket_billing=billing,
                currency=asset,
                defaults={
                    "price_per_token": price,
                    "revenue_account": revenue_account,
                },
            )
            if not tp_created and tp.price_per_token != price:
                tp.price_per_token = price
                tp.revenue_account = revenue_account
                tp.save(update_fields=["price_per_token", "revenue_account"])
                self.stdout.write(f"  ~ updated {unit_name} price → {price} ({note})")
            elif tp_created:
                self.stdout.write(self.style.SUCCESS(
                    f"  + {unit_name}: {price} per STORAGE_TOKEN  ({note})"
                ))
            else:
                self.stdout.write(f"  ~ {unit_name} price already set ({price})")

        self.stdout.write(self.style.SUCCESS("Finance BucketBilling ready."))
