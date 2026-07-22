from django.http import FileResponse, Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import StoredBackup


def api_key_from_request(request):
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.headers.get("X-Api-Key", "").strip()


@require_GET
def stored_backup_stream(request, uid, magic_token):
    try:
        backup = StoredBackup.objects.select_related("platform").get(
            uid=uid,
            magic_token=magic_token,
        )
    except StoredBackup.DoesNotExist:
        raise Http404("Stored backup not found.")

    if not backup.can_be_pulled:
        return JsonResponse({"detail": "Stored backup is not available."}, status=410)

    if not backup.verify_api_key(api_key_from_request(request)):
        return JsonResponse({"detail": "Invalid or missing API key."}, status=403)

    backup.pull_count += 1
    backup.last_pulled_at = timezone.now()
    backup.save(update_fields=["pull_count", "last_pulled_at"])

    response = FileResponse(
        backup.file.open("rb"),
        as_attachment=True,
        filename=backup.file.name.rsplit("/", 1)[-1],
        content_type="application/zip",
    )
    if backup.size_bytes:
        response["Content-Length"] = str(backup.size_bytes)
    response["X-Toto-Backup-SHA256"] = backup.sha256
    response["X-Toto-Backup-UID"] = str(backup.uid)
    return response
