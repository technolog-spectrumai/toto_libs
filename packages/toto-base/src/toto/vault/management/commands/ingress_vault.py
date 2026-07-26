import os

from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.vault.models import Bucket, FileGateway, VaultDirectory, VaultFile


class Command(IngressCommand):
    help = "Seed Vault: always creates default buckets/dirs; full mode adds demo files and gateways."

    def process(self):
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        try:
            user = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"User '{admin_username}' not found — skipping vault ingress."
            ))
            return

        self._ensure_default_structure(user)
        self._ensure_zip_workflow()

        if not self.full:
            return

        self._seed_full_demo(user)

    # ── Always-on: the zip workflow (only when the workflow engine is present) ──

    def _ensure_zip_workflow(self):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            return
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="vault-zip",
            defaults={
                "name": "Zip files",
                "description": "Bundle selected vault files into a single .zip archive saved back to the vault.",
            },
        )
        if created or not wf.nodes.filter(task_name="vault_zip_files").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Zip selected files",
                task_name="vault_zip_files",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("  + workflow: vault-zip"))

    # ── Always-on: default bucket + directory tree ────────────────────────────

    def _ensure_default_structure(self, user):
        bucket_general, _ = Bucket.objects.get_or_create(
            slug="general",
            defaults={"name": "General", "owner": user},
        )

        def mkdir(name, bucket, parent=None):
            d, created = VaultDirectory.objects.get_or_create(
                name=name, bucket=bucket, parent=parent,
                defaults={"owner": user},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + dir {d.full_path()}"))
            return d

        def mkgateway(directory, name, description):
            gw, created = FileGateway.objects.get_or_create(
                directory=directory,
                defaults={"name": name, "description": description, "make_public": False},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + gateway: {name}"))
            return gw

        docs_dir    = mkdir("Documents", bucket_general)
        archive_dir = mkdir("Archive",   bucket_general)

        mkgateway(docs_dir,    "General Documents Upload", "Upload documents to the General bucket.")
        mkgateway(archive_dir, "General Archive Upload",   "Upload files to the Archive.")

        self.stdout.write(self.style.SUCCESS("Default vault structure ready."))

    # ── Full ingress: demo buckets, files, gateways, billing ─────────────────

    def _seed_full_demo(self, user):
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

        if bucket_finance.storage_quota_mb != 500:
            bucket_finance.storage_quota_mb = 500
            bucket_finance.save(update_fields=["storage_quota_mb"])
            self.stdout.write(self.style.SUCCESS("Finance bucket → quota=500 MB"))
        self.stdout.write(self.style.SUCCESS("Buckets ready."))

        # ── Directory tree ────────────────────────────────────────────────────
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

        # ── Demo files ────────────────────────────────────────────────────────
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

        # ── Gateways ──────────────────────────────────────────────────────────
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

        self.stdout.write(self.style.SUCCESS("✅  Vault ingress complete."))
