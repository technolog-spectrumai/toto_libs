"""OCR — a Knowledge-Graph (ravioli) sub-tab.

A deliberately small, stateless flow: upload a screenshot → run Tesseract →
show the text → optionally save the screenshot into a vault bucket (and folder)
→ forward the text to the ingestor. No DB models; the OCR engine is the shared
``OcrHelper`` (pytesseract) and saving reuses the canonical VaultFile create
pattern.
"""
from __future__ import annotations

import os
import tempfile

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def _buckets_and_directories(user):
    """The user's own buckets plus their directories (path label, per bucket).

    Returns ``(buckets, directories)`` where directories is a flat list of
    ``{"pk", "bucket_pk", "label"}`` the template filters by selected bucket.
    Folder paths are built in Python (2 queries) instead of per-row walks.
    """
    from toto.vault.models import Bucket, VaultDirectory

    buckets = list(Bucket.objects.filter(owner=user).order_by("name").values("pk", "name"))

    nodes = {
        d["pk"]: d
        for d in VaultDirectory.objects.filter(bucket__owner=user).values(
            "pk", "name", "parent_id", "bucket_id"
        )
    }

    def _path(pk):
        parts, seen = [], set()
        node = nodes.get(pk)
        while node and node["pk"] not in seen:
            seen.add(node["pk"])
            parts.append(node["name"])
            node = nodes.get(node["parent_id"])
        return "/".join(reversed(parts))

    directories = [
        {"pk": d["pk"], "bucket_pk": d["bucket_id"], "label": _path(d["pk"])}
        for d in nodes.values()
    ]
    directories.sort(key=lambda x: (x["bucket_pk"], x["label"]))
    return buckets, directories


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
    buckets, directories = _buckets_and_directories(request.user)

    # The Ingest button is usable only when the ingestor is installed AND the
    # graph backend is enabled — otherwise the handoff would just 503.
    ingest_enabled = False
    try:
        reverse("ingestor:home")
        from toto.ravioli.connection import is_enabled
        ingest_enabled = bool(is_enabled())
    except (NoReverseMatch, ImportError):
        ingest_enabled = False

    context = {
        "buckets": buckets,
        "directories": directories,  # rendered via json_script (XSS-safe)
        "run_url": reverse("ocr:run"),
        "ingest_enabled": ingest_enabled,
    }
    return render(request, "ocr/ocr.html", PageProcessor().decorate(context, request))


@require_POST
@login_required
@superuser_required
def ocr_run(request):
    """Run Tesseract on an uploaded screenshot; optionally save it to a bucket."""
    from toto.ocr.ocr import OcrHelper
    from toto.vault.models import Bucket, VaultDirectory, VaultFile

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

    # --- Optional save into a vault bucket / folder ---
    saved, file_url = False, None
    if request.POST.get("save_to_vault") in ("on", "true", "1", "yes"):
        bucket = Bucket.objects.filter(pk=request.POST.get("bucket"), owner=request.user).first()
        if bucket is None:
            return JsonResponse({"error": "Pick a bucket you own to save to."}, status=400)

        # A folder is optional, but if given it must belong to the chosen bucket.
        directory = None
        dir_pk = request.POST.get("directory")
        if dir_pk:
            directory = VaultDirectory.objects.filter(pk=dir_pk, bucket=bucket).first()
            if directory is None:
                return JsonResponse(
                    {"error": "That folder is not in the selected bucket."}, status=400
                )

        title = screenshot.name or f"screenshot{ext}"
        vf = VaultFile(
            owner=request.user,
            title=title,
            key=_unique_key(bucket, slugify(os.path.splitext(title)[0])),
            bucket=bucket,
            directory=directory,
            file_type=VaultFile.detect_type(getattr(screenshot, "content_type", "") or "", title),
            is_public=False,
        )
        try:
            vf.file.save(title, ContentFile(data), save=True)
        except IntegrityError:
            # Lost a race on the (bucket, key) unique constraint — the SELECT in
            # _unique_key and this INSERT aren't atomic. Surface a clean error.
            return JsonResponse(
                {"error": "A file with that name was just saved here — try again."},
                status=409,
            )
        try:
            vf.content_hash = vf.create_hash()
            vf.save(update_fields=["content_hash"])
        except Exception:  # noqa: BLE001 — hashing is best-effort
            pass
        saved, file_url = True, vf.get_public_url()

    return JsonResponse({"text": text, "saved": saved, "file_url": file_url})


@require_POST
@login_required
@superuser_required
def ocr_ingest(request):
    """Hand the extracted text to the ingestor.

    Post/Redirect/Get: stash the text in the session and 302 to the ingestor
    page, which pre-fills its textarea and auto-generates a proposal. This lands
    the user on the real review surface (not the JSON ``generate`` endpoint).
    """
    request.session["ingest_text"] = request.POST.get("text") or ""
    try:
        return redirect("ingestor:home")
    except NoReverseMatch:  # ingestor not installed — fall back to the OCR page
        request.session.pop("ingest_text", None)
        return redirect("ocr:home")
