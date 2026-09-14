"""One file at a time between the Vault and a Capsule, for the transfer window.

The Files tab's transfer window copies both ways and shows a progress bar,
and a progress bar needs the browser to see each file finish. So each door
here moves ONE file and answers in JSON, and the window walks the ticked set
one request at a time — the antivirus scan modal's shape, sequential on
purpose: one click fanning out twenty parallel executor round trips is a
stampede the person caused and cannot see.

The two desk doors these replace looped over up to 25 files inside ONE request
and answered with a redirect, so nothing could be shown until everything was
done, and a slow executor held a worker for all of it.

NOTHING HERE DECIDES WHAT A COPY COSTS. Into a Capsule is `transfer.to_capsule`
(free); out of one is `transfer.to_bucket` (metered and scanned exactly like an
upload). The doors add only what a browser door must: session and CSRF,
owner-filtered lookups where somebody else's file, bucket or folder is the
same 404 as none at all, and a `stop` flag — whether every later file in the
batch would be refused the same way, so the window stops instead of repeating
one refusal twenty times.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from . import panes, transfer
from .runtime import RuntimeUnavailable, get_backend
from .views import MAX_UPLOAD_BYTES, _own_open_lease, _vault_rows

#: Refusals about ONE file. Anything else is about the person or the platform
#: — quota, funds, arrears, a runtime that cannot hold files, a destination
#: that has gone — and every later file in the batch would meet it too.
PER_FILE_CODES = frozenset({
    "no_name", "unreadable", "bad_title", "refused_type", "infected",
    "file_refused", "no_such_file", "encrypted", "not_local", "too_large",
    "name_taken",
})


def _refusal(message: str, code: str, status: int) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message, "code": code,
                         "stop": code not in PER_FILE_CODES}, status=status)


def _sentence(exc) -> str:
    return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _transfer_refusal(exc) -> JsonResponse:
    """The three things a transfer can raise, each as JSON with its own status.

    A `TransferRefused` keeps its code (409). The executor's own refusal —
    "that name escapes the files area", "already exists" — is the person's to
    fix and about that file (409 `file_refused`). A runtime that does not
    answer is nobody's fault and stops the batch (503).
    """
    from .executor_backend import FilesRefused

    if isinstance(exc, transfer.TransferRefused):
        return _refusal(_sentence(exc), exc.refusal_code or "refused", 409)
    if isinstance(exc, FilesRefused):
        return _refusal(_sentence(exc), "file_refused", 409)
    return _refusal(_("the compute runtime is not answering (%(detail)s)")
                    % {"detail": _sentence(exc)}, "runtime_unavailable", 503)


@login_required
@require_POST
def copy_in(request, uuid):
    """One Vault file into the Capsule. FREE — nothing durable is created.

    Refused BEFORE a byte is read: somebody else's file (404, naming nothing),
    an encrypted file (the Capsule would receive ciphertext), a file in a
    remote bucket (its bytes are not on this server), and a file over the
    web tier's transfer limit (this process would hold it whole).
    """
    from toto.vault.models import VaultFile

    from .executor_backend import FilesRefused

    lease = _own_open_lease(request, uuid)
    pk = panes.parse_pk(request.POST.get("file"))
    vault_file = (VaultFile.objects.filter(owner=request.user, pk=pk)
                  .select_related("bucket").first() if pk else None)
    if vault_file is None:
        return _refusal(_("no such file"), "no_such_file", 404)
    if vault_file.is_encrypted:
        return _refusal(_("“%(title)s” is encrypted, so the Capsule would receive "
                          "only its ciphertext. Remove the encryption in the Vault "
                          "first.") % {"title": vault_file.title}, "encrypted", 409)
    if vault_file.bucket_id and not vault_file.bucket.is_local:
        return _refusal(_("“%(title)s” is in a remote bucket; only files stored on "
                          "this server can be copied into a Capsule.")
                        % {"title": vault_file.title}, "not_local", 409)
    if (vault_file.file_size_bytes or 0) > MAX_UPLOAD_BYTES:
        return _refusal(_("“%(title)s” is over the %(mb)s MB transfer limit.")
                        % {"title": vault_file.title,
                           "mb": MAX_UPLOAD_BYTES // (1024 * 1024)},
                        "too_large", 413)

    folder = (request.POST.get("folder") or "").strip().strip("/")
    target = f"{folder}/{vault_file.title}" if folder else ""
    try:
        result = transfer.to_capsule(
            lease=lease, vault_file=vault_file, name=target, actor=request.user,
            replace=bool(request.POST.get("replace")))
    except (transfer.TransferRefused, FilesRefused, RuntimeUnavailable) as exc:
        return _transfer_refusal(exc)
    return JsonResponse({
        "ok": True,
        "name": result.get("name") or target or vault_file.title,
        "bytes": result.get("bytes", 0),
        "from": {"id": vault_file.pk, "key": vault_file.key,
                 "title": vault_file.title},
    }, status=201)


@login_required
@require_POST
def copy_out(request, uuid):
    """One Capsule file into a bucket, and a folder in it if one is named.

    METERED AND SCANNED like an upload: `transfer.to_bucket`, the same function
    the bearer API's door calls, so the same price. A destination that is not
    the person's — a bucket, or a folder that is not in that bucket or not
    theirs — is a 404 that names nothing and stops the batch; so is a remote
    bucket, whose files `to_bucket` would otherwise write to this server's disk.
    """
    from toto.vault.models import Bucket

    from .executor_backend import FilesRefused

    lease = _own_open_lease(request, uuid)
    name = (request.POST.get("name") or "").strip()
    if not name:
        return _refusal(_("Name the file to copy out."), "no_name", 400)
    bucket = Bucket.objects.filter(
        owner=request.user, slug=(request.POST.get("bucket") or "").strip()).first()
    if bucket is None:
        return _refusal(_("no such bucket"), "no_such_bucket", 404)
    if not bucket.is_local:
        return _refusal(_("“%(bucket)s” is a remote bucket; files from a Capsule can "
                          "only be copied into a bucket stored on this server.")
                        % {"bucket": bucket.name}, "remote_bucket", 409)
    directory = None
    raw_directory = (request.POST.get("directory") or "").strip()
    if raw_directory:
        directory = panes.resolve_owned_directory(request.user, raw_directory, bucket)
        if directory is None:
            return _refusal(_("no such folder"), "no_such_folder", 404)
    try:
        vault_file = transfer.to_bucket(lease=lease, name=name, bucket=bucket,
                                        actor=request.user, directory=directory)
    except (transfer.TransferRefused, FilesRefused, RuntimeUnavailable) as exc:
        return _transfer_refusal(exc)
    return JsonResponse({
        "ok": True,
        "id": vault_file.pk, "key": vault_file.key, "title": vault_file.title,
        "size": vault_file.file_size_bytes, "bucket": bucket.slug,
        "directory": ({"id": directory.pk, "name": directory.name}
                      if directory is not None else None),
    }, status=201)


@login_required
@require_GET
def rows(request, uuid):
    """Both panes, fresh, for the window to redraw after a batch.

    The Capsule side is listed only while MOUNTED, like the Files tab: reading
    the area needs the executor. `answered: false` is "nobody answered", which
    the window must not draw as an empty area.
    """
    from . import choices, services

    lease = _own_open_lease(request, uuid)
    listing = {}
    backend = get_backend()
    if (services.capsule_report(lease)["state"] in choices.MOUNTED
            and getattr(backend, "capsule_files", None) is not None):
        listing = backend.capsule_files(lease) or {}
    return JsonResponse({
        "vault": panes.vault_pane_rows(request.user),
        "capsule": {
            "items": _vault_rows(listing.get("files", [])),
            "answered": bool(listing),
            "complete": bool(listing.get("complete", False)),
        },
    })
