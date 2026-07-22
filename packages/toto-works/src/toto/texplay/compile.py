from __future__ import annotations

import os
import subprocess
import tempfile

from django.core.files.base import ContentFile
from django.utils.text import slugify


def compile_tex_file(vault_file) -> tuple[bytes, str]:
    """
    Compile a single-file .tex VaultFile to PDF via pdflatex.
    Returns (pdf_bytes, full_log).  Raises RuntimeError on failure.
    """
    with vault_file.file.open("rb") as fh:
        tex_bytes = fh.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "main.tex"), "wb") as f:
            f.write(tex_bytes)

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "main.tex"],
            cwd=tmpdir,
            capture_output=True,
            timeout=30,
        )
        log = (result.stdout + result.stderr).decode("utf-8", errors="replace")

        pdf_path = os.path.join(tmpdir, "main.pdf")
        if not os.path.exists(pdf_path):
            raise RuntimeError(f"pdflatex did not produce a PDF.\n{log}")

        with open(pdf_path, "rb") as f:
            return f.read(), log


def save_compiled_pdf(vault_file, pdf_bytes: bytes):
    """
    Upsert the compiled PDF as a VaultFile in the same bucket/directory.
    Returns the VaultFile instance.
    """
    from toto.vault.models import VaultFile

    base = vault_file.key or os.path.splitext(vault_file.title or "document")[0]
    pdf_key = (slugify(f"{base}-compiled") or "compiled")[:250]
    pdf_title = f"{os.path.splitext(vault_file.title or 'document')[0]}.pdf"

    existing = VaultFile.objects.filter(
        bucket=vault_file.bucket, key=pdf_key
    ).first()

    if existing:
        existing.file.save(f"{pdf_key}.pdf", ContentFile(pdf_bytes), save=True)
        return existing

    pdf_vf = VaultFile(
        owner=vault_file.owner,
        title=pdf_title,
        bucket=vault_file.bucket,
        directory=vault_file.directory,
        file_type="pdf",
        is_public=vault_file.is_public,
        key=pdf_key,
    )
    pdf_vf.file.save(f"{pdf_key}.pdf", ContentFile(pdf_bytes), save=False)
    pdf_vf.file_size_bytes = len(pdf_bytes)
    pdf_vf.save()
    return pdf_vf
