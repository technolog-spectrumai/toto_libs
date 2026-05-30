from toto.ingress import IngressCommand
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from toto.texlab.models import LatexWorkspace, LatexFile
from toto.vault.models import Bucket, VaultFile

User = get_user_model()


class Command(IngressCommand):
    help = "Seed TexLab with a default workspace, bucket, and sample files"

    def process(self):
        self._check_tariff()
        self._ensure_workflows()

        if not self.full:
            return

        self.stdout.write(self.style.WARNING("🧪 Seeding TexLab…"))

        # ---------------------------------------------------------
        # 1) Find user named 'admin'
        # ---------------------------------------------------------
        try:
            user = User.objects.get(username="admin")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("❌ User 'admin' does not exist — cannot seed TexLab"))
            return

        self.stdout.write(self.style.SUCCESS(f"👤 Using user: {user.username}"))

        # ---------------------------------------------------------
        # 2) Create a dedicated TexLab bucket
        # ---------------------------------------------------------
        bucket, created = Bucket.objects.get_or_create(
            name="TexLab Workspace Bucket",
            owner=user,
            defaults={"slug": "texlab-bucket"}
        )

        if created:
            self.stdout.write(self.style.SUCCESS("🪣 Created TexLab bucket"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️ TexLab bucket already exists"))

        # ---------------------------------------------------------
        # 3) Create default workspace
        # ---------------------------------------------------------
        ws, created = LatexWorkspace.objects.get_or_create(
            bucket=bucket,
            name="Sample LaTeX Workspace"
        )

        if created:
            self.stdout.write(self.style.SUCCESS("📁 Created workspace: Sample LaTeX Workspace"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️ Workspace already exists"))

        # ---------------------------------------------------------
        # 4) Create sample VaultFiles (using ContentFile)
        # ---------------------------------------------------------

        # --- main.tex ---
        main_tex_content = r"""
\documentclass{article}

\begin{document}
Hello from TexLab!
\end{document}
        """.strip()

        main_vault, created = VaultFile.objects.get_or_create(
            owner=user,
            bucket=bucket,
            title="main.tex",
        )

        if created or not main_vault.file:
            main_vault.file.save("main.tex", ContentFile(main_tex_content))
            main_vault.save()
            self.stdout.write(self.style.SUCCESS("📝 Created VaultFile: main.tex"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️ VaultFile main.tex already exists"))

        LatexFile.objects.get_or_create(
            workspace=ws,
            vault_file=main_vault,
            defaults={"file_type": "tex"}
        )

        # --- sample.sty ---
        sty_content = r"""
% Sample style file
\ProvidesPackage{sample}[2024/01/01 Sample style]
        """.strip()

        sty_vault, created = VaultFile.objects.get_or_create(
            owner=user,
            bucket=bucket,
            title="sample.sty",
        )

        if created or not sty_vault.file:
            sty_vault.file.save("sample.sty", ContentFile(sty_content))
            sty_vault.save()
            self.stdout.write(self.style.SUCCESS("🎨 Created VaultFile: sample.sty"))

        LatexFile.objects.get_or_create(
            workspace=ws,
            vault_file=sty_vault,
            defaults={"file_type": "sty"}
        )

        # --- references.bib ---
        bib_content = r"""
@book{example,
  title={Example Book},
  author={Doe, John},
  year={2024},
  publisher={TexLab Press}
}
        """.strip()

        bib_vault, created = VaultFile.objects.get_or_create(
            owner=user,
            bucket=bucket,
            title="references.bib",
        )

        if created or not bib_vault.file:
            bib_vault.file.save("references.bib", ContentFile(bib_content))
            bib_vault.save()
            self.stdout.write(self.style.SUCCESS("📚 Created VaultFile: references.bib"))

        LatexFile.objects.get_or_create(
            workspace=ws,
            vault_file=bib_vault,
            defaults={"file_type": "bib"}
        )

        # ---------------------------------------------------------
        # Done
        # ---------------------------------------------------------
        self.stdout.write(self.style.SUCCESS("✅ TexLab seeding complete."))

    def _check_tariff(self):
        from django.apps import apps as django_apps
        if not django_apps.is_installed("toto.tariffs"):
            self.stdout.write("  toto.tariffs not installed — skipping tariff check.")
            return
        from toto.tariffs.models import Tariff
        if Tariff.objects.filter(code="TEXLAB-STANDARD").exists():
            self.stdout.write("  [texlab] TEXLAB-STANDARD tariff: ready.")
        else:
            self.stdout.write(self.style.WARNING(
                "  [texlab] TEXLAB-STANDARD tariff not found — run ingress_tariffs first."
            ))

    def _ensure_workflows(self):
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="texlab-compile-latex",
            defaults={
                "name": "TexLab Compile LaTeX",
                "description": "Compile a LaTeX file to PDF and store the result.",
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Compile LaTeX",
                task_name="texlab_compile_latex",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📄 Created workflow: TexLab Compile LaTeX"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'TexLab Compile LaTeX' already exists"))
