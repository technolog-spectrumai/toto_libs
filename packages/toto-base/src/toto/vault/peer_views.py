"""The peer API — the server half of the bucket link.

Serves one exported bucket to one authenticated peer, whole: directory ACLs do
not cross hosts (half-replicating per-user whitelists would fail open or
silently narrow; the exporting operator grants the bucket or does not).

**Auth is magic-token-in-path plus an api key header, cheap check first.**
The URL segment is the unguessable part: resolving it is one indexed lookup,
so a guessed URL costs a miss, not key stretching. PBKDF2 verification of
``X-Vault-Api-Key`` runs strictly last (~100ms at the project's iteration
count — the SSO provider once saturated at ~35 req/s because everything cheap
did not run first). ``csrf_exempt`` per the clearing server-to-server
convention: the caller is a host, not a browser with cookies.

**404 for an unknown grant, honesty for a held one.** A pair that resolves no
row must be indistinguishable from a URL that never existed. But an expired or
revoked grant answers 403 with a sentence — the caller demonstrably HOLDS the
credential, so existence is no secret, and the sentence is what lets the far
operator fix the pairing.

**Encrypted non-PDF files never cross.** They are Fernet-sealed under THIS
instance's strongbox salt; the ciphertext is permanently unopenable anywhere
else, so serving it would be handing over garbage that looks like a file.
409, with the reason. (Encrypted PDFs carry standard portable PDF encryption
and cross fine.)
"""
import mimetypes
import os

from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import scanning as _scanning
from . import storage_backends as _storage_backends
from .models import VaultFile
from .peering import BUCKET_RIGHTS, BucketGrant, has_bucket_right
from .purge import purge_file
from .views import _unique_file_key

API_KEY_HEADER = "X-Vault-Api-Key"

#: Server-side page cap for the listing. The client may ask for less, never
#: for more.
PAGE_SIZE = 200


def _plain(text, status):
    return HttpResponse(text, status=status,
                        content_type="text/plain; charset=utf-8")


def _resolve_grant(request, grant_uid, magic_token):
    """(grant, None) on success, (None, response) on refusal.

    The order is the contract: indexed pair lookup, then active/expiry flags,
    then — only then — the PBKDF2 key check. Audit stamps go through a
    queryset ``.update()`` so a read never races a concurrent admin save.
    """
    grant = (BucketGrant.objects.select_related("bucket", "bucket__owner")
             .filter(grant_uid=grant_uid, magic_token=magic_token).first())
    if grant is None:
        raise Http404("No such grant.")
    if not grant.can_be_used:
        return None, _plain(
            "Grant expired or revoked — mint a new pairing code on the "
            "exporting host.", 403)
    if not grant.verify_api_key(request.headers.get(API_KEY_HEADER, "")):
        return None, _plain("Bad api key.", 403)
    BucketGrant.objects.filter(pk=grant.pk).update(
        last_read_at=timezone.now(), read_count=F("read_count") + 1,
        last_peer_ip=request.META.get("REMOTE_ADDR") or None)
    return grant, None


def _no_right(right):
    return JsonResponse(
        {"error": f"This grant does not include {right}."}, status=403)


def _row(f):
    return {
        "key": f.key,
        "title": f.title,
        "file_type": f.file_type,
        "size": f.file_size_bytes,
        "hash": f.content_hash,
        "is_encrypted": f.is_encrypted,
        "is_public": f.is_public,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


@csrf_exempt
@require_http_methods(["GET"])
def peer_manifest(request, grant_uid, magic_token):
    """Who is answering, for which bucket, with which rights. (may_list)"""
    grant, err = _resolve_grant(request, grant_uid, magic_token)
    if err:
        return err
    if not has_bucket_right(grant, "may_list"):
        return _no_right("may_list")
    bucket = grant.bucket
    return JsonResponse({
        "site_name": os.environ.get("PLATFORM_NAME", ""),
        "bucket": bucket.slug,
        "bucket_name": bucket.name,
        "rights": [r for r in BUCKET_RIGHTS if getattr(grant, r)],
        "total_files": VaultFile.objects.filter(bucket=bucket).count(),
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def peer_files(request, grant_uid, magic_token):
    """GET: keyset-paged listing (may_list). POST: upload (may_upload)."""
    grant, err = _resolve_grant(request, grant_uid, magic_token)
    if err:
        return err
    if request.method == "POST":
        return _upload(request, grant)

    if not has_bucket_right(grant, "may_list"):
        return _no_right("may_list")
    try:
        cursor = int(request.GET.get("cursor") or 0)
        page_size = min(int(request.GET.get("page_size") or PAGE_SIZE),
                        PAGE_SIZE)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "cursor and page_size must be integers."}, status=400)
    qs = VaultFile.objects.filter(bucket=grant.bucket).order_by("pk")
    # Keyset, not OFFSET: the mirror walks every page of large buckets, and a
    # row created mid-walk must shift nothing already read.
    rows = list(qs.filter(pk__gt=cursor)[:page_size + 1])
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    return JsonResponse({
        "files": [_row(f) for f in rows],
        "total": qs.count(),
        "next_cursor": rows[-1].pk if (has_more and rows) else None,
    })


def _upload(request, grant):
    if not has_bucket_right(grant, "may_upload"):
        return _no_right("may_upload")
    uploaded = request.FILES.get("file")
    if uploaded is None:
        return JsonResponse(
            {"error": "Send one file in a multipart field named 'file'."},
            status=400)
    if uploaded.size > _storage_backends.EXTERNAL_UPLOAD_MAX_BYTES:
        cap_mib = _storage_backends.EXTERNAL_UPLOAD_MAX_BYTES // (2 ** 20)
        return JsonResponse(
            {"error": f"File is too large for a cross-host upload "
                      f"(max {cap_mib} MiB)."}, status=413)

    # The exporter owns what lands in their bucket — the storage levy bills
    # the exporting operator, consistent with them granting the space.
    owner = grant.bucket.owner
    mime, _ = mimetypes.guess_type(uploaded.name)
    file_type = VaultFile.detect_type(mime or "", uploaded.name)

    verdict = _scanning.Verdict.clean(scanned=False)
    if _scanning.should_scan(owner, file_type, door="peer"):
        body = uploaded.read()
        uploaded.seek(0)
        verdict = _scanning.scan(body, file_type=file_type,
                                 filename=uploaded.name)
        if not verdict.ok:
            return JsonResponse(verdict.as_error(), status=400)

    vault_file = VaultFile(
        owner=owner,
        title=uploaded.name,
        key=_unique_file_key(
            slugify(os.path.splitext(uploaded.name)[0]), grant.bucket),
        file_type=file_type,
        bucket=grant.bucket,
        directory=None,
        is_public=False,
    )
    try:
        _storage_backends.persist_upload(vault_file, uploaded)
    except _storage_backends.UploadRefused as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _scanning.record(vault_file, verdict, user=None, door="peer")
    return JsonResponse({"ok": True, **_row(vault_file)}, status=201)


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def peer_file_detail(request, grant_uid, magic_token, key):
    """GET: one file's metadata (may_list). DELETE: purge it (may_delete)."""
    grant, err = _resolve_grant(request, grant_uid, magic_token)
    if err:
        return err
    right = "may_delete" if request.method == "DELETE" else "may_list"
    if not has_bucket_right(grant, right):
        return _no_right(right)
    file_obj = VaultFile.objects.filter(bucket=grant.bucket, key=key).first()
    if file_obj is None:
        raise Http404("No such file.")
    if request.method == "DELETE":
        purge_file(file_obj)
        return JsonResponse({"ok": True, "deleted": key})
    return JsonResponse(_row(file_obj))


@csrf_exempt
@require_http_methods(["GET", "HEAD"])
def peer_file_download(request, grant_uid, magic_token, key):
    """Stream one file's bytes (may_download). HEAD answers size and hash."""
    grant, err = _resolve_grant(request, grant_uid, magic_token)
    if err:
        return err
    if not has_bucket_right(grant, "may_download"):
        return _no_right("may_download")
    file_obj = VaultFile.objects.filter(bucket=grant.bucket, key=key).first()
    if file_obj is None:
        raise Http404("No such file.")
    if file_obj.is_encrypted and file_obj.file_type != "pdf":
        return JsonResponse({
            "error": "This file is encrypted under this host's local salt "
                     "and would be unopenable anywhere else.",
            "reason": "encrypted-non-portable",
        }, status=409)
    if request.method == "HEAD":
        resp = HttpResponse()
        resp["Content-Length"] = str(file_obj.file_size_bytes or 0)
        resp["X-Vault-Hash"] = file_obj.content_hash or ""
        resp["X-Vault-File-Type"] = file_obj.file_type or ""
        return resp
    try:
        stream = _storage_backends.open_file_stream(file_obj)
    except Exception as exc:  # noqa: BLE001 — a dead backend must not traceback
        label = file_obj.bucket.name if file_obj.bucket_id else "its storage"
        return _plain(
            f"'{file_obj.title}' could not be fetched from {label}: "
            f"{type(exc).__name__}: {exc}", 502)
    return FileResponse(
        stream, as_attachment=True,
        filename=os.path.basename(file_obj.file.name) or file_obj.key)
