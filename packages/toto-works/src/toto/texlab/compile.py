from __future__ import annotations

import os
import subprocess
import tempfile

from django.core.files.base import ContentFile


def _extract_latex_error(log: str) -> str:
    """The meaningful part of a pdfTeX log.

    pdfTeX prints its version banner first and the real failure much later, so a
    head-truncated log just shows "This is pdfTeX, Version …". Pull the ``! …``
    error line(s) plus a little following context (pdfTeX prints the message, then
    the offending source line and ``l.<n>``). Falls back to the log tail when no
    explicit error marker is present.
    """
    lines = (log or "").splitlines()
    blocks = [
        "\n".join(lines[i:i + 6]).rstrip()
        for i, line in enumerate(lines)
        if line.startswith("!")
    ]
    if blocks:
        return "\n\n".join(blocks).strip()
    nonblank = [ln for ln in lines if ln.strip()]
    return "\n".join(nonblank[-15:]).strip() or "pdflatex produced no output."


def run_pdflatex(tex_source: str, vault_file) -> tuple[bytes, str]:
    """
    Compile *tex_source* via pdflatex.  Companion files (latex/text and images)
    are gathered from the same VaultDirectory (bucket root when no directory).
    Returns (pdf_bytes, full_log).  Raises RuntimeError when pdflatex produces
    no PDF.
    """
    from toto.vault.models import VaultFile

    dir_filter = {"bucket": vault_file.bucket, "directory": vault_file.directory}

    companions = VaultFile.objects.filter(
        **dir_filter, file_type__in=["latex", "text"]
    ).exclude(pk=vault_file.pk)

    images = VaultFile.objects.filter(**dir_filter, file_type="image")

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "main.tex"), "w", encoding="utf-8") as f:
            f.write(tex_source)

        placed: set[str] = set()

        for companion in companions:
            filename = companion.title or companion.key
            if not filename or filename in placed:
                continue
            try:
                with companion.file.open("rb") as src, open(os.path.join(tmpdir, filename), "wb") as dst:
                    dst.write(src.read())
                placed.add(filename)
            except Exception:
                pass

        for img in images:
            filename = os.path.basename(img.title or img.key or "")
            if not filename or filename in placed:
                continue
            try:
                with img.file.open("rb") as src, open(os.path.join(tmpdir, filename), "wb") as dst:
                    dst.write(src.read())
                placed.add(filename)
            except Exception:
                pass

        pdf_path = os.path.join(tmpdir, "main.pdf")
        log = ""
        try:
            # Two passes so \ref/\tableofcontents/\cite resolve. 60s covers a
            # container's slow first run (font-cache build). A fatal error in pass
            # one leaves no PDF → skip the pointless second pass.
            for _ in range(2):
                proc = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "main.tex"],
                    cwd=tmpdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
                log = proc.stdout.decode(errors="ignore") + "\n" + proc.stderr.decode(errors="ignore")
                if not os.path.exists(pdf_path):
                    break
        except FileNotFoundError:
            # Surface a clear, actionable log instead of a raw [Errno 2].
            raise RuntimeError(
                "pdflatex is not installed on this host — the TeX toolchain is "
                "missing. Deployments need INSTALL_TEXLIVE=1 (rebuild the image "
                "so texlive is baked in, then redeploy); locally install it via "
                "your package manager (e.g. `apt install texlive-latex-base`)."
            )
        except subprocess.TimeoutExpired as exc:
            partial = (getattr(exc, "output", None) or b"")
            partial = partial.decode(errors="ignore") if isinstance(partial, bytes) else str(partial)
            raise RuntimeError(
                "pdflatex timed out after 60s — a container's very first compile can "
                "be slow while the font cache builds; retry once. "
                + _extract_latex_error(partial)
            )

        if not os.path.exists(pdf_path):
            # An empty document body compiles cleanly but yields zero pages (and
            # so no PDF). Say that plainly instead of dumping the pdfTeX banner.
            if "No pages of output" in log and "!" not in log:
                summary = (
                    "The document produced no pages — it has no content. Add text "
                    "between \\begin{document} and \\end{document}, then compile again."
                )
            else:
                # Lead with the actual '! ...' error so the (truncated) UI shows the
                # real problem instead of the pdfTeX startup banner.
                summary = _extract_latex_error(log)
            raise RuntimeError(summary + "\n\n--- full pdflatex log ---\n" + log)

        with open(pdf_path, "rb") as f:
            return f.read(), log


def get_or_create_pdf_vaultfile(vault_file):
    """Get-or-create the output PDF VaultFile in the same bucket/directory."""
    from toto.vault.models import VaultFile

    base, _ = os.path.splitext(vault_file.title or vault_file.key or "document")
    pdf_filename = f"{base}.pdf"
    pdf_key = f"{base}-pdf"

    try:
        pdf_vault = VaultFile.objects.get(bucket=vault_file.bucket, key=pdf_key)
    except VaultFile.DoesNotExist:
        pdf_vault = VaultFile(
            owner=vault_file.owner,
            bucket=vault_file.bucket,
            directory=vault_file.directory,
            title=pdf_filename,
            key=pdf_key,
            file_type="pdf",
            is_public=vault_file.is_public,
        )
        pdf_vault.save()

    return pdf_vault, pdf_filename


def compile_tex_to_pdf(vault_file):
    """Compile a .tex VaultFile → (pdf_VaultFile, log)."""
    with vault_file.file.open("r") as f:
        tex_source = f.read()
    pdf_bytes, log = run_pdflatex(tex_source, vault_file)
    pdf_vault, pdf_filename = get_or_create_pdf_vaultfile(vault_file)
    pdf_vault.file.save(pdf_filename, ContentFile(pdf_bytes), save=True)
    return pdf_vault, log
