import hashlib
import json
import os

from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils.text import slugify

from toto.api.cors import CorsApiView
from toto.vault.models import VaultFile, Bucket, VaultDirectory

# Text-ish file types editable in the Enigma Ace editor. Mirrors the file types
# the toto editor app handles, kept deliberately narrow (no binary/media).
EDITABLE_FILE_TYPES = {
    "text", "json", "yaml", "xml", "csv", "latex", "bib", "python", "svg", "html",
}
# Refuse to load very large files into the editor.
MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB


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
        "directory_id": vf.directory_id,
        "is_editable": (vf.file_type in EDITABLE_FILE_TYPES) and not vf.is_encrypted,
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


def _resolve_owned_bucket(user, slug):
    """The user's bucket with this slug, or None. Bucket.slug is globally unique,
    so we look it up by slug and verify ownership — a foreign bucket returns None
    (→ 404) rather than trying (and failing) to create a duplicate slug."""
    return Bucket.objects.filter(slug=slug, owner=user).first()


def _resolve_owned_directory(user, directory_id, bucket):
    """The user's directory (id, in `bucket`), or None. Tolerates non-numeric and
    out-of-range ids (int() overflow would otherwise 500 on SQLite)."""
    try:
        pk = int(directory_id)
    except (ValueError, TypeError):
        return None
    if pk < 0 or pk > 9223372036854775807:  # signed 64-bit ceiling
        return None
    try:
        return VaultDirectory.objects.get(pk=pk, bucket=bucket, owner=user)
    except (VaultDirectory.DoesNotExist, ValueError, TypeError, OverflowError):
        return None


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
        file_type = VaultFile.detect_type(content_type, file.name)

        bucket_slug = request.POST.get("bucket_slug", "").strip()
        if bucket_slug:
            bucket = _resolve_owned_bucket(request.user, bucket_slug)
            if bucket is None:
                return JsonResponse({"error": "Bucket not found."}, status=404)
        else:
            bucket = _get_or_create_default_bucket(request.user)

        directory = None
        directory_id = request.POST.get("directory_id", "").strip()
        if directory_id:
            directory = _resolve_owned_directory(request.user, directory_id, bucket)
            if directory is None:
                return JsonResponse({"error": "Directory not found."}, status=404)

        content = file.read()
        content_hash = hashlib.sha256(content).hexdigest()
        file.seek(0)

        # Keys address files across the whole owner (the detail/download/delete/move
        # endpoints look up by key alone), so keep them unique per owner — not just
        # per bucket — or a cross-bucket collision makes those lookups ambiguous.
        base_key = slugify(title) or slugify(os.path.splitext(file.name)[0]) or "file"
        key = base_key
        counter = 1
        while VaultFile.objects.filter(owner=request.user, key=key).exists():
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
            directory=directory,
        )
        vf.file.save(file.name, file, save=True)

        return JsonResponse(_file_to_dict(request, vf), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class FileDetailApiView(CorsApiView):
    def _get_file(self, user, key):
        # .first() (not .get()) so a legacy duplicate key never raises
        # MultipleObjectsReturned → 500; new uploads are deduped per owner.
        return (
            VaultFile.objects.select_related("bucket").filter(owner=user, key=key).first()
        )

    def get(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)
        return JsonResponse(_file_to_dict(request, vf))

    def patch(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        update_fields = []
        if "title" in data:
            title = str(data["title"]).strip()
            if not title:
                return JsonResponse({"error": "Title cannot be empty."}, status=400)
            vf.title = title
            update_fields.append("title")
        # "directory_id" present with null → move to bucket root; with an int →
        # move into that directory. Key absent → leave the directory unchanged.
        if "directory_id" in data:
            directory_id = data["directory_id"]
            if directory_id is None:
                vf.directory = None
            else:
                directory = _resolve_owned_directory(request.user, directory_id, vf.bucket)
                if directory is None:
                    return JsonResponse({"error": "Directory not found."}, status=404)
                vf.directory = directory
            update_fields.append("directory")
        if update_fields:
            vf.save(update_fields=update_fields)
        return JsonResponse(_file_to_dict(request, vf))

    def delete(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        # Scope to the owner up front: an unscoped get(key=key) would 500 on a key
        # another user also owns (MultipleObjectsReturned) and leak existence.
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)

        vf.delete()
        return JsonResponse({}, status=204)


@method_decorator(csrf_exempt, name="dispatch")
class FileEncryptApiView(CorsApiView):
    def post(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            vf = VaultFile.objects.select_related("bucket").get(key=key, owner=request.user)
        except VaultFile.DoesNotExist:
            return JsonResponse({"error": "File not found."}, status=404)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        password = str(data.get("password", "")).strip()
        if not password:
            return JsonResponse({"error": "Password required."}, status=400)
        if vf.is_encrypted:
            return JsonResponse({"error": "File is already encrypted."}, status=400)
        try:
            vf.encrypt(password=password)
            vf.is_public = False
            vf.save(update_fields=["is_public"])
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
        return JsonResponse(_file_to_dict(request, vf))


@method_decorator(csrf_exempt, name="dispatch")
class FileDecryptApiView(CorsApiView):
    def post(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            vf = VaultFile.objects.select_related("bucket").get(key=key, owner=request.user)
        except VaultFile.DoesNotExist:
            return JsonResponse({"error": "File not found."}, status=404)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        password = str(data.get("password", "")).strip()
        if not password:
            return JsonResponse({"error": "Password required."}, status=400)
        if not vf.is_encrypted:
            return JsonResponse({"error": "File is not encrypted."}, status=400)
        try:
            vf.decrypt(password=password)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
        return JsonResponse(_file_to_dict(request, vf))


@method_decorator(csrf_exempt, name="dispatch")
class VaultMetricsApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        from django.utils import timezone
        from datetime import timedelta, date
        from django.db.models import Count, Sum, Q
        from django.db.models.functions import TruncDate
        from toto.vault.models import Bucket

        user = request.user
        qs = VaultFile.objects.filter(owner=user)

        total_files = qs.count()
        public_files = qs.filter(is_public=True).count()
        encrypted_files = qs.filter(is_encrypted=True).count()
        total_size = qs.aggregate(s=Sum("file_size_bytes"))["s"] or 0
        week_ago = timezone.now() - timedelta(days=7)
        recent_count = qs.filter(uploaded_at__gte=week_ago).count()

        files_by_type = list(
            qs.values("file_type").annotate(count=Count("id")).order_by("-count")
        )

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_map = {
            e["day"]: e["count"]
            for e in qs.filter(uploaded_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("uploaded_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        today = date.today()
        daily_series = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": daily_map.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        bucket_stats = []
        for b in Bucket.objects.filter(owner=user).annotate(
            file_count=Count("files", distinct=True),
            public_count=Count("files", filter=Q(files__is_public=True), distinct=True),
            encrypted_count=Count("files", filter=Q(files__is_encrypted=True), distinct=True),
            total_size=Sum("files__file_size_bytes"),
        ).order_by("name"):
            bucket_stats.append({
                "slug": b.slug,
                "name": b.name,
                "file_count": b.file_count,
                "public_count": b.public_count,
                "encrypted_count": b.encrypted_count,
                "total_size": b.total_size or 0,
            })

        return JsonResponse({
            "total_files": total_files,
            "public_files": public_files,
            "encrypted_files": encrypted_files,
            "total_size_bytes": total_size,
            "recent_count": recent_count,
            "files_by_type": files_by_type,
            "daily_series": daily_series,
            "bucket_stats": bucket_stats,
        })


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


@method_decorator(csrf_exempt, name="dispatch")
class BucketTreeApiView(CorsApiView):
    """The user's storage skeleton: buckets and their nested directories.

    Files are NOT included here — the client overlays them from the file-list
    endpoint using each file's ``bucket_slug`` + ``directory_id``. Together they
    reproduce the toto storage tree (bucket → folders → files).
    """

    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        # Buckets the user owns, plus any bucket holding one of their files.
        owned = Bucket.objects.filter(owner=request.user)
        from_files = Bucket.objects.filter(files__owner=request.user)
        buckets = owned.union(from_files).order_by("name")

        dirs = (
            VaultDirectory.objects.filter(owner=request.user)
            .select_related("parent")
            .order_by("name")
        )
        dirs_by_bucket = {}
        for d in dirs:
            dirs_by_bucket.setdefault(d.bucket_id, []).append(d)

        out = []
        for b in buckets:
            out.append({
                "id": b.id,
                "slug": b.slug,
                "name": b.name,
                "directories": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "parent_id": d.parent_id,
                        "path": d.full_path(),
                    }
                    for d in dirs_by_bucket.get(b.id, [])
                ],
            })
        return JsonResponse({"buckets": out})


@method_decorator(csrf_exempt, name="dispatch")
class DirectoryCreateApiView(CorsApiView):
    """`POST /vault/api/directories/` — create a folder (Enigma Cloud mkdir).

    JSON: ``{bucket_slug?, name, parent_id?}`` → 201
    ``{id, name, parent_id, path, bucket_slug}``. ``bucket_slug`` omitted →
    the user's default personal bucket (same helper as upload).
    """

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        name = str(data.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Name is required."}, status=400)

        bucket_slug = str(data.get("bucket_slug") or "").strip()
        if bucket_slug:
            try:
                bucket = Bucket.objects.get(slug=bucket_slug, owner=request.user)
            except Bucket.DoesNotExist:
                return JsonResponse({"error": "Bucket not found."}, status=404)
        else:
            bucket = _get_or_create_default_bucket(request.user)

        parent = None
        parent_id = data.get("parent_id")
        if parent_id is not None:
            parent = _resolve_owned_directory(request.user, parent_id, bucket)
            if parent is None:
                return JsonResponse({"error": "Parent directory not found."}, status=404)

        if VaultDirectory.objects.filter(bucket=bucket, parent=parent, name=name).exists():
            return JsonResponse({"error": "Directory already exists."}, status=409)

        directory = VaultDirectory.objects.create(
            name=name, bucket=bucket, owner=request.user, parent=parent
        )
        return JsonResponse(
            {
                "id": directory.id,
                "name": directory.name,
                "parent_id": directory.parent_id,
                "path": directory.full_path(),
                "bucket_slug": bucket.slug,
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class DirectoryDeleteApiView(CorsApiView):
    """`DELETE /vault/api/directories/<pk>/` — remove an EMPTY folder (rmdir)."""

    def delete(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        # <int:pk> matches unbounded digits; a huge value would overflow the SQLite
        # column and 500. Range-check before hitting the DB.
        if pk < 0 or pk > 9223372036854775807:
            return JsonResponse({"error": "Directory not found."}, status=404)
        try:
            directory = VaultDirectory.objects.get(pk=pk, owner=request.user)
        except (VaultDirectory.DoesNotExist, OverflowError):
            return JsonResponse({"error": "Directory not found."}, status=404)

        if directory.files.exists() or directory.subdirectories.exists():
            return JsonResponse({"error": "Directory is not empty."}, status=409)

        directory.delete()
        return JsonResponse({}, status=204)


@method_decorator(csrf_exempt, name="dispatch")
class FileContentApiView(CorsApiView):
    """Read / write the text content of an editable vault file (Ace editor).

    GET  → ``{content, file_type, is_editable, size}``
    PUT  → save new ``content`` and return the refreshed file dict.

    Only non-encrypted, text-ish files are accepted; binary/media and encrypted
    files are rejected so we never hand back mojibake or clobber ciphertext.
    """

    def _get_file(self, user, key):
        # .first() (not .get()) so a legacy duplicate key never raises
        # MultipleObjectsReturned → 500; new uploads are deduped per owner.
        return (
            VaultFile.objects.select_related("bucket").filter(owner=user, key=key).first()
        )

    def get(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)
        if vf.is_encrypted:
            return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=400)
        if vf.file_type not in EDITABLE_FILE_TYPES:
            return JsonResponse({"error": "This file type is not editable."}, status=415)
        if vf.file_size_bytes and vf.file_size_bytes > MAX_EDIT_BYTES:
            return JsonResponse({"error": "File is too large to edit."}, status=413)
        try:
            vf.file.open("rb")
            try:
                raw = vf.file.read()
            finally:
                vf.file.close()
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return JsonResponse({"error": "File is not valid UTF-8 text."}, status=415)
        except Exception as e:
            return JsonResponse({"error": f"Could not read file: {e}"}, status=500)
        return JsonResponse({
            "key": vf.key,
            "title": vf.title,
            "file_type": vf.file_type,
            "is_editable": True,
            "size": vf.file_size_bytes,
            "content": content,
        })

    def put(self, request, key):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        vf = self._get_file(request.user, key)
        if not vf:
            return JsonResponse({"error": "File not found."}, status=404)
        if vf.is_encrypted:
            return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=400)
        if vf.file_type not in EDITABLE_FILE_TYPES:
            return JsonResponse({"error": "This file type is not editable."}, status=415)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        content = data.get("content")
        if not isinstance(content, str):
            return JsonResponse({"error": "content (string) is required."}, status=400)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_EDIT_BYTES:
            return JsonResponse({"error": "Content is too large to save."}, status=413)
        try:
            with vf.file.open("w") as f:
                f.write(content)
            vf.file_size_bytes = len(encoded)
            vf.content_hash = hashlib.sha256(encoded).hexdigest()
            vf.save(update_fields=["file_size_bytes", "content_hash"])
        except Exception as e:
            return JsonResponse({"error": f"Could not save file: {e}"}, status=500)
        return JsonResponse(_file_to_dict(request, vf))


@method_decorator(csrf_exempt, name="dispatch")
class FileCreateApiView(CorsApiView):
    """Create an empty editable vault file and return it (Enigma/Aurora New-file).

    JSON: ``{bucket_slug, directory_id?, title, file_type}`` → the new file dict
    (incl. ``key``), so the client can open it straight in its Ace editor.

    Gated by EDITABLE_FILE_TYPES rather than the server's web editor plugins: the
    desktop client edits text-ish files in its own Ace editor, so e.g. latex stays
    creatable here even on a server whose web latex editor isn't installed.
    """

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        from toto.vault.views import CreateEmptyFileView, create_empty_vault_file

        title = (data.get("title") or "").strip()
        file_type = (data.get("file_type") or "").strip()
        bucket_slug = (data.get("bucket_slug") or "").strip()
        directory_id = data.get("directory_id")

        if not title:
            return JsonResponse({"error": "Filename is required."}, status=400)
        # Creatable (has starter content) AND editable in the client's Ace editor.
        creatable = set(CreateEmptyFileView._INITIAL) & EDITABLE_FILE_TYPES
        if file_type not in creatable:
            return JsonResponse({"error": f"Cannot create an editable {file_type or '?'} file."}, status=400)
        if not bucket_slug:
            return JsonResponse({"error": "bucket_slug is required."}, status=400)

        try:
            bucket = Bucket.objects.get(slug=bucket_slug, owner=request.user)
        except Bucket.DoesNotExist:
            return JsonResponse({"error": "Bucket not found."}, status=404)

        directory = None
        if directory_id not in (None, "", 0, "0"):
            try:
                directory = VaultDirectory.objects.get(pk=int(directory_id), bucket=bucket)
            except (VaultDirectory.DoesNotExist, ValueError, TypeError):
                return JsonResponse({"error": "Directory not found."}, status=404)

        vf = create_empty_vault_file(request.user, bucket, directory, title, file_type)
        return JsonResponse(_file_to_dict(request, vf), status=201)
