import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.notarius import contract_format as cf
from toto.notarius.defaults import DEFAULT_CONTRACT_LATEX
from toto.notarius.models import ContractTemplate


class Command(IngressCommand):
    help = "Seed a default contract LaTeX template (always) and a sample .contract (--full)."

    def process(self):
        # The default LaTeX template is seeded ALWAYS (even without --full): these are
        # tedious to write by hand, so a working one should exist out of the box.
        self._seed_default_template()
        if self.full:
            self._seed_sample_contract()

    # ── Always-on ─────────────────────────────────────────────────────────────

    def _seed_default_template(self):
        tpl, created = ContractTemplate.objects.get_or_create(
            key="default",
            defaults={
                "name": "Default contract",
                "description": (
                    "Cover/summary page (metadata, parties, signatures with handwritten "
                    "appearance images, audit trail), then the embedded original document "
                    "via \\includepdf. Copy this per contract type and customize."
                ),
                "latex_source": DEFAULT_CONTRACT_LATEX,
                "is_default": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Exists'}: ContractTemplate 'default'"
        ))

    # ── Demo (--full) ─────────────────────────────────────────────────────────

    def _seed_sample_contract(self):
        from toto.vault.models import Bucket, VaultDirectory, VaultFile

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        try:
            user = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"User '{admin_username}' not found — skipping sample .contract."
            ))
            return

        bucket, _ = Bucket.objects.get_or_create(
            slug="general", defaults={"name": "General", "owner": user},
        )
        directory, _ = VaultDirectory.objects.get_or_create(
            name="Documents", bucket=bucket, parent=None, defaults={"owner": user},
        )

        key = "sample-contract"
        if VaultFile.objects.filter(bucket=bucket, key=key).exists():
            self.stdout.write("Exists: sample contract")
            return

        contract = cf.new_contract(
            title="Sample Service Agreement",
            issuer_name="Spectrum AI Sp. z o.o.",
            issuer_email="technology@spectrumai.pl",
            doc_type="default",
        )
        contract.parties[0].representative = cf.Representative(
            id="rep-1", name="Jan Kowalski", title="Authorized Representative", email="jan@example.com",
        )
        contract.parties.append(cf.Party(
            id="party-2", type="person", role="signer",
            legal_name="Anna Nowak", email="anna@example.com",
        ))
        contract.content = cf.Content(
            id="content-1", media_type="text/plain", encoding="text",
            data=("This Service Agreement is entered into between the Issuer and the Signer. "
                  "The parties agree to the terms set out herein. Open the editor to change "
                  "the parties or content, then sign to record an electronic + handwritten "
                  "signature, and convert to PDF via the admin LaTeX template."),
        )
        xml = cf.dumps(contract).encode("utf-8")

        vf = VaultFile(
            owner=user, title="Sample Contract.xml", key=key,
            file_type="xml", bucket=bucket, directory=directory,
            is_public=True, notes="Demo signing document seeded by ingress_notarius.",
        )
        vf.file.save(f"{slugify(key)}.xml", ContentFile(xml), save=False)
        vf.file_size_bytes = len(xml)
        vf.save()
        self.stdout.write(self.style.SUCCESS("Created: sample contract (Documents/Sample Contract.xml)"))
