import os
import subprocess
import tempfile
from django.core.files.base import ContentFile
from toto.vault.models import VaultFile
from toto.texlab.models import LatexFile


def run_pdflatex(tex_source, workspace):
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "main.tex")
        pdf_path = os.path.join(tmpdir, "main.pdf")

        # Write source
        with open(tex_path, "w") as f:
            f.write(tex_source)

        placed = set()  # track filenames already written

        # Copy all workspace files into tmpdir
        for lf in LatexFile.objects.filter(workspace=workspace).select_related("vault_file"):
            src = lf.vault_file.file
            filename = lf.vault_file.key  # real filename
            dst_path = os.path.join(tmpdir, filename)
            with src.open("rb") as fsrc, open(dst_path, "wb") as fdst:
                fdst.write(fsrc.read())
            placed.add(filename)

        # Also copy all images from the workspace bucket so that
        # \includegraphics{filename} resolves without manual workspace entry.
        # Files are placed using their title (which preserves the extension).
        bucket_images = VaultFile.objects.filter(
            bucket=workspace.bucket,
            file_type="image",
        )
        for img in bucket_images:
            filename = os.path.basename(img.title or img.key)
            if not filename or filename in placed:
                continue
            dst_path = os.path.join(tmpdir, filename)
            try:
                with img.file.open("rb") as fsrc, open(dst_path, "wb") as fdst:
                    fdst.write(fsrc.read())
                placed.add(filename)
            except Exception:
                pass  # don't fail the compile if one image is unreadable

        # Compile
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "main.tex"],
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )

        # Full log (stdout + stderr)
        log = proc.stdout.decode(errors="ignore") + "\n" + proc.stderr.decode(errors="ignore")

        # If no PDF → return log
        if not os.path.exists(pdf_path):
            raise RuntimeError(log)

        # Return PDF bytes + log
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        return pdf_bytes, log



def get_or_create_pdf_vaultfile(vault_file, workspace):
    original_base = vault_file.title or vault_file.key
    original_base, _ext = os.path.splitext(original_base)

    pdf_filename = f"{original_base}.pdf"
    pdf_key = f"{original_base}-pdf"

    bucket = workspace.bucket

    try:
        pdf_vault = VaultFile.objects.get(bucket=bucket, key=pdf_key)
    except VaultFile.DoesNotExist:
        pdf_vault = VaultFile(
            owner=vault_file.owner,
            bucket=bucket,
            title=pdf_filename,
            key=pdf_key,
            file_type="pdf",
            is_public=True,
        )
        pdf_vault.save()

        # Attach to workspace once
        LatexFile.objects.get_or_create(
            workspace=workspace,
            vault_file=pdf_vault,
            defaults={"file_type": "pdf"}
        )

    return pdf_vault, pdf_filename


def compile_tex_to_pdf(vault_file, workspace):
    with vault_file.file.open("r") as f:
        tex_source = f.read()
    pdf_bytes, log = run_pdflatex(tex_source, workspace)
    pdf_vault, pdf_filename = get_or_create_pdf_vaultfile(vault_file, workspace)
    pdf_vault.file.save(pdf_filename, ContentFile(pdf_bytes), save=True)
    return pdf_vault, log

