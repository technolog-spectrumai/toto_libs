from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.vault.models import Bucket, FileGateway, VaultDirectory, VaultFile


class Command(IngressCommand):
    help = "Seed Vault demo data: buckets, directories, files, gateways, tariff wiring."

    def process(self):
        if not self.full:
            return

        # ── Users ─────────────────────────────────────────────────────────────
        try:
            user = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise CommandError("Demo user 'admin' not found. Run ingress_core first.")

        # ── Billing cycles (owned by invoice app) ─────────────────────────────
        from toto.invoice.models import BillingCycle, BillingCycleFrequency

        cycle_monthly, _ = BillingCycle.objects.get_or_create(
            name="Monthly",
            defaults={"frequency": BillingCycleFrequency.MONTHLY, "description": "Invoice once per calendar month.", "active": True},
        )
        cycle_quarterly, _ = BillingCycle.objects.get_or_create(
            name="Quarterly",
            defaults={"frequency": BillingCycleFrequency.MONTHLY, "description": "Invoice once per quarter (approx. every 3 months).", "active": True},
        )
        cycle_yearly, _ = BillingCycle.objects.get_or_create(
            name="Yearly",
            defaults={"frequency": BillingCycleFrequency.YEARLY, "description": "Annual invoice.", "active": True},
        )
        self.stdout.write(self.style.SUCCESS("Billing cycles ready."))

        # ── FILE-STORAGE tariff ────────────────────────────────────────────────
        from toto.tariffs.models import Tariff

        try:
            tariff_storage = Tariff.objects.get(code="FILE-STORAGE")
        except Tariff.DoesNotExist:
            raise CommandError(
                "Tariff 'FILE-STORAGE' not found. Run `python manage.py ingress_tariffs` first."
            )

        # ── Buckets ───────────────────────────────────────────────────────────
        bucket_legal, _ = Bucket.objects.get_or_create(
            slug="legal",
            defaults={"name": "Legal", "owner": user},
        )

        bucket_finance, _ = Bucket.objects.get_or_create(
            slug="finance",
            defaults={"name": "Finance", "owner": user},
        )

        bucket_media, _ = Bucket.objects.get_or_create(
            slug="media",
            defaults={"name": "Media", "owner": user},
        )

        # Wire tariff + quota to Finance bucket — update even if bucket existed
        changed = False
        if bucket_finance.tariff_id != tariff_storage.pk:
            bucket_finance.tariff = tariff_storage
            changed = True
        if bucket_finance.storage_quota_mb != 500:
            bucket_finance.storage_quota_mb = 500
            changed = True
        if changed:
            bucket_finance.save(update_fields=["tariff", "storage_quota_mb"])
            self.stdout.write(self.style.SUCCESS(
                f"Finance bucket → tariff={tariff_storage.code}, quota=500 MB"
            ))
        else:
            self.stdout.write(f"Finance bucket tariff already wired ({tariff_storage.code}).")

        self.stdout.write(self.style.SUCCESS("Buckets ready."))

        # ── Directory tree ─────────────────────────────────────────────────────
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

        # Legal
        contracts  = mkdir("Contracts",  bucket_legal)
        mkdir("Vendors",   bucket_legal, parent=contracts)
        mkdir("Clients",   bucket_legal, parent=contracts)
        compliance = mkdir("Compliance", bucket_legal, restricted=True)

        # Finance
        reports      = mkdir("Reports",  bucket_finance)
        mkdir("Q1 2026",   bucket_finance, parent=reports)
        mkdir("Q2 2026",   bucket_finance, parent=reports)
        invoices_dir = mkdir("Invoices", bucket_finance, restricted=True)

        # Media
        logos_dir   = mkdir("Logos",   bucket_media)
        archive_dir = mkdir("Archive", bucket_media)

        self.stdout.write(self.style.SUCCESS("Directory tree ready."))

        # ── Demo files ─────────────────────────────────────────────────────────
        def mkfile(title, bucket, directory, file_type="text", notes=""):
            key = slugify(title)
            if VaultFile.objects.filter(bucket=bucket, key=key).exists():
                return
            vf = VaultFile(
                owner=user, title=title, key=key,
                file_type=file_type, bucket=bucket,
                directory=directory, is_public=True, notes=notes,
            )
            vf.file.save(f"{key}.txt", ContentFile(f"Demo content for {title}\n".encode()), save=False)
            vf.save()
            self.stdout.write(self.style.SUCCESS(f"  + file {title}"))

        contracts_dir = VaultDirectory.objects.get(name="Contracts", bucket=bucket_legal, parent=None)
        vendors_dir   = VaultDirectory.objects.get(name="Vendors",   bucket=bucket_legal, parent=contracts_dir)
        clients_dir   = VaultDirectory.objects.get(name="Clients",   bucket=bucket_legal, parent=contracts_dir)

        mkfile("Master Service Agreement", bucket_legal, contracts_dir, "pdf",  "Signed 2025-01")
        mkfile("Vendor NDA - Acme Corp",   bucket_legal, vendors_dir,   "pdf",  "Confidential")
        mkfile("Vendor NDA - BuildRight",  bucket_legal, vendors_dir,   "pdf",  "Confidential")
        mkfile("Client Contract - Stark",  bucket_legal, clients_dir,   "pdf",  "Active")
        mkfile("Client Contract - Wayne",  bucket_legal, clients_dir,   "pdf",  "Active")
        mkfile("GDPR Assessment 2025",     bucket_legal, compliance,    "pdf",  "Internal only")
        mkfile("Audit Report Q4 2025",     bucket_legal, compliance,    "pdf",  "Restricted")

        q1 = VaultDirectory.objects.get(name="Q1 2026", bucket=bucket_finance)
        q2 = VaultDirectory.objects.get(name="Q2 2026", bucket=bucket_finance)
        mkfile("Revenue Summary Q1 2026",  bucket_finance, q1,           "pdf",  "Board approved")
        mkfile("Cost Breakdown Q1 2026",   bucket_finance, q1,           "json", "Exported from ERP")
        mkfile("Revenue Summary Q2 2026",  bucket_finance, q2,           "pdf",  "Draft")
        mkfile("Invoice INV-2026-001",     bucket_finance, invoices_dir, "pdf",  "Paid")
        mkfile("Invoice INV-2026-002",     bucket_finance, invoices_dir, "pdf",  "Pending")

        mkfile("Logo Primary",             bucket_media, logos_dir,   "svg", "Primary brand mark")
        mkfile("Logo Mono",                bucket_media, logos_dir,   "svg", "Single-colour variant")
        mkfile("Brand Guidelines 2024",    bucket_media, archive_dir, "pdf", "Superseded by 2025 version")

        self.stdout.write(self.style.SUCCESS("Demo files seeded."))

        # ── Gateways ───────────────────────────────────────────────────────────
        for directory, name, description in [
            (contracts_dir, "Legal Contracts Upload",  "Submit new vendor or client contracts."),
            (reports,       "Finance Reports Upload",   "Submit quarterly financial reports."),
            (logos_dir,     "Media Logos Upload",       "Upload brand logo assets."),
        ]:
            gw, created = FileGateway.objects.get_or_create(
                directory=directory,
                defaults={"name": name, "description": description, "make_public": True},
            )
            gw.allowed_users.set([user])
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + gateway: {name}"))

        self.stdout.write(self.style.SUCCESS("Gateways ready."))

        # ── Seed demo invoice for Finance bucket ───────────────────────────────
        from toto.invoice.models import Invoice, InvoiceStatus
        from datetime import date
        from decimal import Decimal

        if not Invoice.objects.filter(bucket=bucket_finance).exists():
            Invoice.objects.create(
                issued_to=user,
                issued_by=user,
                bucket=bucket_finance,
                billing_cycle=cycle_monthly,
                title=f"Storage Invoice — Finance — {date.today().strftime('%B %Y')}",
                description=(
                    f"Monthly storage billing for the Finance bucket "
                    f"under tariff {tariff_storage.code}."
                ),
                amount=Decimal("0.00"),
                currency_label="STORAGE_TOKEN",
                status=InvoiceStatus.PENDING,
            )
            self.stdout.write(self.style.SUCCESS(
                "Demo invoice created for Finance bucket (amount=0.00, pending — use Generate Invoice to issue real ones)."
            ))
        else:
            self.stdout.write("Finance bucket already has invoices — skipping demo invoice.")

        self.stdout.write(self.style.SUCCESS("✅  Vault ingress complete."))
