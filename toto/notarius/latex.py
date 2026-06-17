"""
Contract -> LaTeX -> PDF.

The LaTeX is produced from an admin-editable :class:`ContractTemplate` chosen by the
contract's ``<documentType>`` (so each contract type can render differently). The
template is rendered with the Django template engine, then compiled with ``pdflatex``
(requires a TeX install / ``BUILD_LABS``). When ``<content>`` is a base64 PDF it is
materialized as ``content.pdf`` so the template can ``\\includepdf`` the original.
"""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile

from django.core.files.base import ContentFile
from django.template import engines
from django.utils.text import slugify

from toto.notarius import contract_format


def _decode_b64(data: str) -> bytes:
    data = (data or "").strip()
    if data.lower().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def render_latex(contract, template_source: str, *, signature_images, content_pdf_filename: str) -> str:
    """Render the admin LaTeX template against the contract context.

    HTML autoescaping is forced OFF (LaTeX is not HTML) — text safety is the
    template author's job via the ``|latexescape`` filter.
    """
    wrapped = "{% autoescape off %}" + template_source + "{% endautoescape %}"
    tpl = engines["django"].from_string(wrapped)
    return tpl.render({
        "contract": contract,
        "signature_images": signature_images,
        "content_pdf_filename": content_pdf_filename,
    })


def contract_to_pdf(vault_file) -> tuple[bytes, str]:
    """
    Compile ``vault_file`` (a ``.contract``) to PDF using the template for its type.
    Returns ``(pdf_bytes, log)``. Raises ``RuntimeError`` on failure.
    """
    from toto.notarius.models import ContractTemplate

    raw = vault_file.file.read().decode("utf-8")
    contract = contract_format.loads(raw)

    template = ContractTemplate.for_type(contract.doc_type)
    if template is None:
        raise RuntimeError(
            "No ContractTemplate is configured. Add one in the admin "
            "(or run `manage.py ingress_notarius`)."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Handwritten signature appearances -> sig-<id>.png in the build dir.
        signature_images = []
        for sig in contract.signatures:
            img_b64 = sig.appearance.image_b64 if sig.appearance else ""
            filename = ""
            if img_b64:
                filename = f"sig-{slugify(sig.id) or 'x'}.png"
                with open(os.path.join(tmpdir, filename), "wb") as f:
                    f.write(_decode_b64(img_b64))
            party = contract.party_by_id(sig.party)
            signature_images.append({
                "id": sig.id,
                "party_name": party.legal_name if party else sig.party,
                "typed_name": sig.appearance.typed_name if sig.appearance else "",
                "signed_at": sig.signed_at,
                "method": sig.method,
                "filename": filename,
            })

        # Embedded original document -> content.pdf (for \includepdf).
        content_pdf_filename = ""
        if (contract.content.media_type == "application/pdf"
                and contract.content.encoding == "base64"
                and contract.content.data.strip()):
            content_pdf_filename = "content.pdf"
            with open(os.path.join(tmpdir, content_pdf_filename), "wb") as f:
                f.write(_decode_b64(contract.content.data))

        tex = render_latex(
            contract, template.latex_source,
            signature_images=signature_images,
            content_pdf_filename=content_pdf_filename,
        )
        with open(os.path.join(tmpdir, "main.tex"), "w", encoding="utf-8") as f:
            f.write(tex)

        # Two passes so \includepdf page counts / refs settle.
        log = ""
        for _ in range(2):
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "main.tex"],
                cwd=tmpdir, capture_output=True, timeout=60,
            )
            log = (result.stdout + result.stderr).decode("utf-8", errors="replace")

        pdf_path = os.path.join(tmpdir, "main.pdf")
        if not os.path.exists(pdf_path):
            raise RuntimeError(f"pdflatex did not produce a PDF.\n{log[-3000:]}")
        with open(pdf_path, "rb") as f:
            return f.read(), log


def save_contract_pdf(vault_file, pdf_bytes: bytes):
    """Upsert the compiled PDF as a sibling VaultFile (mirrors texplay.save_compiled_pdf)."""
    from toto.vault.models import VaultFile

    base = vault_file.key or os.path.splitext(vault_file.title or "contract")[0]
    pdf_key = (slugify(f"{base}-signed") or "contract")[:250]
    pdf_title = f"{os.path.splitext(vault_file.title or 'contract')[0]}.pdf"

    existing = VaultFile.objects.filter(bucket=vault_file.bucket, key=pdf_key).first()
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
