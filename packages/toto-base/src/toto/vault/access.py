"""Who may read a vault file, and the edit-guard for encrypted ones.

**``may_read`` is the one rule, and it lives here because it has to be one.**
It was written in ``toto/fileservices/access.py`` — a module in
**toto-media-ops**, a wheel zenobia does not pin — so the vault, which owns the
files, could not use its own policy. The vault's download view therefore had a
rule of its own: *are you logged in at all*, which let any account fetch any
private file by bucket slug and key. That is the hole this module closes, and
keeping a second copy is how it would come back.

fileservices and manta now import from here; their old module is a re-export so
nothing outside had to move at once.

Encrypted vault files hold ciphertext, so they must never open in an editor. The
per-app editor views call :func:`encrypted_lock_response` at their entry point to
return a friendly "decrypt it first" page (HTTP 403) instead of the editor, mirroring
how the vault UI already hides the Edit button for encrypted files.
"""

from __future__ import annotations

from django.shortcuts import render


def may_read(user, vault_file) -> bool:
    """May this user read these bytes?

    Superuser, owner, public, bucket owner, or a directory ACL. The same five
    clauses ``accessible_files`` uses as a queryset — this is the per-object
    twin, for the paths that already hold one row.

    **Anonymous gets the public arm only.** Nothing else: an unauthenticated
    request has no ownership and no ACL membership to check.
    """
    if vault_file is None:
        return False
    if user is None or not getattr(user, "is_authenticated", False):
        return bool(vault_file.is_public)
    if user.is_superuser:
        return True
    if vault_file.owner_id == user.id:
        return True
    if vault_file.is_public:
        return True
    if vault_file.bucket_id and vault_file.bucket.owner_id == user.id:
        return True
    if vault_file.directory_id and vault_file.directory.allowed_users.filter(
            pk=user.pk).exists():
        return True
    return False


def encrypted_lock_response(request, vault_file=None):
    """Render the 'file is encrypted — decrypt it first' page with HTTP 403.

    Used as the entry guard in every editor view. Returns a full HttpResponse so a
    view can ``return encrypted_lock_response(request, vf)`` directly.
    """
    from toto.ui import PageProcessor

    context = PageProcessor().decorate(
        {
            "vault_file": vault_file,
            "vault_url": _vault_url(),
        },
        request,
    )
    return render(request, "vault/encrypted_locked.html", context, status=403)


def _vault_url() -> str:
    from django.urls import reverse, NoReverseMatch

    try:
        return reverse("vault:public_list")
    except NoReverseMatch:
        return "/"


# ---------------------------------------------------------------------------
# Where the bytes are
# ---------------------------------------------------------------------------

def is_local_content(vault_file) -> bool:
    """True when this file's bytes are on this host's disk.

    The content-rewrite gate. Reading crosses the wire happily (downloads
    stream through the bucket driver); REWRITING must not — editors, zip,
    encrypt and versions all assume a local handle they can reopen, diff and
    replace, and a remote file there would be someone else's bytes edited at
    a distance. Pair with :data:`LOCAL_CONTENT_Q` exactly as ``may_read``
    pairs with ``accessible_files``: one rule, two spellings, or the listing
    and the door drift apart.
    """
    if vault_file is None:
        return False
    if vault_file.bucket_id is None:
        return True
    return vault_file.bucket.is_local


def local_content_q():
    """The queryset spelling of :func:`is_local_content`.

    A function, not a module constant: building a ``Q`` imports nothing at
    module load and keeps this importable before apps are ready.
    """
    from django.db.models import Q

    from toto.vault.models import StorageBackend

    return (Q(bucket__isnull=True)
            | Q(bucket__storage_backend="")
            | Q(bucket__storage_backend=StorageBackend.LOCAL))


def remote_lock_response(request, vault_file):
    """The refusal a content-rewrite surface returns for a non-local file.

    Mirrors the encrypted-file lock: a plain page naming where the bytes
    live and the door that still works (download), never a traceback.
    """
    from django.http import HttpResponse

    label = ""
    if vault_file.bucket_id:
        label = vault_file.bucket.name
    return HttpResponse(
        f"'{vault_file.title}' lives in a remote bucket"
        f"{f' ({label})' if label else ''} — its bytes are not on this host, "
        "so it cannot be edited here. Downloading it still works.",
        status=403, content_type="text/plain; charset=utf-8")
