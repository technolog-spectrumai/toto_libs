import hashlib
import os

from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils.text import slugify

from toto.telegraph.api_views import CorsApiView
from toto.vault.models import VaultFile, Bucket


def _file_to_dict(request, vf):
    try:
        download_url = request.build_absolute_uri(vf.file.url) if vf.file else None
    except Exception:
        download_url = None
    return {
        "id": vf.id,
        "title": vf.title,
        "key": vf.key,
        "file_type": vf.file_type,
        "file_size_bytes": vf.file_size_bytes,
        "is_public": vf.is_public,
        "is_encrypted": vf.is_encrypted,
        "uploaded_at": vf.uploaded_at.isoformat(),
        "download_url": download_url,
        "bucket_slug": vf.bucket.slug if vf.bucket else None,
    }


def _get_or_create_default_bucket(user):
    bucket, _ = Bucket.objects.get_or_create(
        owner=user,
        slug=f"personal-{user.username}",
        defaults={
            "name": f"Personal — {user.username}",
            "storage_backend": "local",
        },
    )
    return bucket


@method_decorator(csrf_exempt, name="dispatch")
class FileListApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        files = (
            VaultFile.objects.filter(owner=request.user)
            .select_related("bucket")
            .order_by("-uploaded_at")[:200]
        )
        return JsonResponse({"files": [_file_to_dict(request, f) for f in files]})


@method_decorator(csrf_exempt, name="dispatch")
class FileUploadApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        file = request.FILES.get("file")
        if not file:
            return JsonResponse({"error": "No file provided."}, status=400)

        title = request.POST.get("title", "").strip() or os.path.splitext(file.name)[0]
        content_type = file.content_type or ""
        file_type = VaultFile.detect_type(content_type)

        bucket = _get_or_create_default_bucket(request.user)

        content = file.read()
        content_hash = hashlib.sha256(content).hexdigest()
        file.seek(0)

        base_key = slugify(title) or slugify(os.path.splitext(file.name)[0]) or "file"
        key = base_key
        counter = 1
        while VaultFile.objects.filter(bucket=bucket, key=key).exists():
            key = f"{base_key}-{counter}"
            counter += 1

        vf = VaultFile(
            owner=request.user,
            title=title,
            key=key,
            file_type=file_type,
            content_hash=content_hash,
            file_size_bytes=file.size,
            bucket=bucket,
        )
        vf.file.save(file.name, file, save=True)

        return JsonResponse(_file_to_dict(request, vf), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class FileDetailApiView(CorsApiView):
    def _get_file(self, user, key):
        try:
            return VaultFile.objects.select_related("bucket").get(owner=user, key=key)
        except VaultFile.DoesNotExist:
            return None

    def get(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)
        return JsonResponse(_file_to_dict(request, vf))

    def delete(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            vf = VaultFile.objects.get(key=key)
        except VaultFile.DoesNotExist:
            return JsonResponse({"error": "File not found."}, status=404)

        if vf.owner != request.user:
            return JsonResponse({"error": "Forbidden."}, status=403)

        vf.delete()
        return JsonResponse({}, status=204)


@method_decorator(csrf_exempt, name="dispatch")
class FileDownloadApiView(CorsApiView):
    def get(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            vf = VaultFile.objects.get(key=key, owner=request.user)
        except VaultFile.DoesNotExist:
            return JsonResponse({"error": "File not found."}, status=404)
        return HttpResponseRedirect(vf.file.url)
