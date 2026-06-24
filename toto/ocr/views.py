"""OCR — a Knowledge-Graph (ravioli) sub-tab.

A deliberately small, stateless flow: upload a screenshot → run Tesseract →
show the text → optionally save the screenshot into a vault bucket → forward the
text to the ingestor. No DB models; the OCR engine is the shared ``OcrHelper``
(pytesseract) and saving reuses the canonical VaultFile create pattern.
"""
from __future__ import annotations

import os
import tempfile

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def _user_buckets(user):
    from toto.vault.models import Bucket

    return list(Bucket.objects.filter(owner=user).order_by("name").values("pk", "name"))


def _unique_key(bucket, base_key):
    from toto.vault.models import VaultFile

    base_key = base_key or "screenshot"
    key, i = base_key, 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        key = f"{base_key}-{i}"
        i += 1
    return key


@login_required
@superuser_required
def ocr_home(request):
    """Render the OCR tab. The whole flow runs client-side against ``ocr:run``."""
    try:
        ingest_generate_url = reverse("ingestor:generate")
    except Exception:  # ingestor not installed — Ingest button stays disabled
        ingest_generate_url = ""

    context = {
        "buckets": _user_buckets(request.user),
        "run_url": reverse("ocr:run"),
        "ingest_generate_url": ingest_generate_url,
    }
    return render(request, "ocr/ocr.html", PageProcessor().decorate(context, request))


@require_POST
@login_required
@superuser_required
def ocr_run(request):
    """Run Tesseract on an uploaded screenshot; optionally save it to a bucket."""
    from toto.ocr.ocr import OcrHelper
    from toto.vault.models import Bucket, VaultFile

    screenshot = request.FILES.get("screenshot")
    if screenshot is None:
        return JsonResponse({"error": "No screenshot uploaded."}, status=400)

    language = (request.POST.get("language") or "").strip() or "eng"
    data = screenshot.read()
    ext = os.path.splitext(screenshot.name or "")[1] or ".png"

    # --- Tesseract ---
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        helper = OcrHelper(language)
        lines = helper.extract_lines(helper.run_tesseract(tmp_path))
        text = "\n".join(line["text"] for line in lines).strip()
    except Exception as exc:  # noqa: BLE001 — surface the tesseract error to the UI
        return JsonResponse({"error": f"OCR failed: {exc}"}, status=500)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # --- Optional save into a vault bucket ---
    saved, file_url = False, None
    if request.POST.get("save_to_vault") in ("on", "true", "1", "yes"):
        bucket = Bucket.objects.filter(pk=request.POST.get("bucket"), owner=request.user).first()
        if bucket is None:
            return JsonResponse({"error": "Pick a bucket you own to save to."}, status=400)
        title = screenshot.name or f"screenshot{ext}"
        vf = VaultFile(
            owner=request.user,
            title=title,
            key=_unique_key(bucket, slugify(os.path.splitext(title)[0])),
            bucket=bucket,
            file_type=VaultFile.detect_type(getattr(screenshot, "content_type", "") or "", title),
            is_public=False,
        )
        vf.file.save(title, ContentFile(data), save=True)
        try:
            vf.content_hash = vf.create_hash()
            vf.save(update_fields=["content_hash"])
        except Exception:  # noqa: BLE001 — hashing is best-effort
            pass
        saved, file_url = True, vf.get_public_url()

    return JsonResponse({"text": text, "saved": saved, "file_url": file_url})
