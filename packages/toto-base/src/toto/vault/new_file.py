"""The vault's "New": one file, named in one field (stage 62b, 2026-10-06).

The page's New button opens a modal with a file name and nothing else; the
name's ending says what the file is. This module is the rule for that name,
the list of endings on offer, and the door that makes the file.

**What is offered** is asked of the editor plugins, as everything about
editors is: a plugin names the endings a new file of its type may carry
(``VaultEditorPlugin.new_file_extensions``) and what such a file starts with
(``new_file_content``), because only the editor knows what it can open. An
ending is offered to a member only where its plugin is mounted on this host
and open to them (``is_open_to``: a plan that lacks the editor is offered
nothing, and the door answers 402). A host with no such plugin has no New.

**The name is refused, never mended.** One name: no slash or backslash, no
``..``, no control character, no space at either end, not empty, not longer
than :data:`MAX_NAME_LENGTH`, and an ending from the list. The type the file
gets is the one an upload of that name would get (``VaultFile.detect_type``),
and a plugin that names an ending for another type offers nothing with it.

**The file is made the way an upload is**: the same right to the folder (the
member's own bucket and folder, and a bucket kept to clearances the member
does not hold is missing), the same quota and funds checks before anything is
written, the one write door (``storage_backends.persist_upload``), the same
two metrics recorded and charged afterwards, and a ``FILE_CREATED`` record in
the audit trail. A name already in the folder is refused with 409 and nothing
is written: the vault itself lets two files share a title, this door does
not.

On a storage-only host the door exists only where the host names ``"new"`` in
``VAULT_STORAGE_ONLY_OPENS`` (``models.storage_only_opens``).
"""

from __future__ import annotations

import hashlib
import logging
import re

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, JsonResponse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View

from .models import (Bucket, VaultDirectory, VaultFile, file_edits_allowed,
                     storage_only_opens, upload_refusal)
from .plugins import VaultEditorPlugin, open_to

logger = logging.getLogger(__name__)

#: The longest name the door takes. A title holds 255; a name typed into one
#: field has no reason to be longer than this.
MAX_NAME_LENGTH = 120

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def _plugins():
    """Every ending on offer on this host -> its plugin. Only a plugin whose
    editor is mounted here, only an ending that starts with a dot, and only
    where an upload of such a name would get the plugin's own type."""
    offered = {}
    if not (storage_only_opens("new") and file_edits_allowed()):
        return offered
    for plugin in VaultEditorPlugin.all():
        endings = getattr(plugin, "new_file_extensions", ()) or ()
        if not endings or not plugin.file_type or not plugin.is_available():
            continue
        for ending in endings:
            ending = str(ending).lower()
            if (len(ending) > 1 and ending.startswith(".")
                    and VaultFile.detect_type("", "x" + ending) == plugin.file_type):
                offered.setdefault(ending, plugin)
    return offered


def offered_extensions(user=None) -> list:
    """The endings a new file may be named with, sorted. ``user`` narrows the
    list to the plugins open to that member; ``None`` asks for the host's."""
    return sorted(ending for ending, plugin in _plugins().items()
                  if open_to(plugin, user))


def ending_of(name: str, endings) -> str:
    """The offered ending ``name`` carries, the longest one first
    (``.sheet.json`` before ``.json``), or ''. A name that is nothing but
    the ending has none: a file needs a name before its dot."""
    lowered = name.lower()
    for ending in sorted(endings, key=len, reverse=True):
        if lowered.endswith(ending) and len(name) > len(ending):
            return ending
    return ""


def unsupported_sentence(endings) -> str:
    return "%s %s" % (_("This file type cannot be made here. Use one of:"),
                      ", ".join(sorted(endings)))


def name_refusal(name: str) -> str:
    """'' when ``name`` is one plain file name, otherwise the sentence to
    show. The ending is the caller's next question."""
    if not name:
        return _("Give the file a name.")
    if (name != name.strip() or "/" in name or "\\" in name or ".." in name
            or _CONTROL.search(name) or name.startswith(".")):
        return _("A file name is one name: no slash, no “..”, no control "
                 "character, no dot at its start and no space at its ends.")
    if len(name) > MAX_NAME_LENGTH:
        # The number is in the sentence, not put into it: the page says the
        # same sentence from its own copy (tests_new_file pins the two).
        return _("This name is too long: at most 120 characters.")
    return ""


def _refuse(sentence, status, **more):
    return JsonResponse({"error": str(sentence), **more}, status=status)


def _folder(user, directory_id):
    """``(bucket, directory)`` the member may put a file into — the upload
    door's own rule — or ``None``. No folder named: the top of the member's
    own bucket, as an upload without one."""
    from .api_views import _get_or_create_default_bucket, _resolve_owned_directory

    if directory_id in (None, ""):
        return _get_or_create_default_bucket(user), None
    try:
        pk = int(directory_id)
    except (TypeError, ValueError):
        return None
    if pk < 0 or pk > 9223372036854775807:
        return None
    directory = VaultDirectory.objects.select_related("bucket").filter(pk=pk).first()
    if directory is None or directory.bucket.owner_id != user.pk:
        return None
    if _resolve_owned_directory(user, pk, directory.bucket) is None:
        return None
    return directory.bucket, directory


def _audit(request, vault_file):
    """One FILE_CREATED record naming the new file. The audit middleware
    cannot name it (the id is in no address), so the door writes the record
    and says so; a refusal is left to the middleware."""
    from django.apps import apps

    from .trash import AUDITED_ATTR

    if not apps.is_installed("toto.audit"):
        return
    setattr(request, AUDITED_ATTR, True)
    from toto.audit import record

    try:
        record(
            "FILE_CREATED",
            app_label="vault",
            object_type="VaultFile",
            object_id=str(vault_file.pk),
            description="vault:new_file",
            actor_user=request.user,
            request=request,
            source="vault",
            success=True,
            # Ids and the type only — never a title: the chain is kept forever.
            metadata={"url_name": "new_file", "method": request.method,
                      "status": 201, "bucket_id": vault_file.bucket_id,
                      "file_type": vault_file.file_type},
        )
    except Exception:  # noqa: BLE001 - the trail must never undo the act
        logger.exception("Could not append the new file to the audit trail.")


class NewFileView(View):
    """POST ``name`` and, for a folder, ``directory_id``; 201 with
    ``{status, file_pk, title, file_type}``, or a JSON refusal whose
    ``error`` is the sentence the modal shows under the name."""

    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if not storage_only_opens("new"):
            # No such door on a host that only stores files and names no New.
            raise Http404("This host stores uploaded files only.")
        if not request.user.is_authenticated:
            return _refuse(_("Not authenticated."), 401)
        from toto.api.fetch_metadata import cross_site_refusal

        refusal = cross_site_refusal(request)
        if refusal is not None:
            return refusal
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        user = request.user
        if not file_edits_allowed():
            return _refuse(_("File editing is disabled on this host."), 403)

        # The plan first: a member no editor is open to is told that, not
        # which names would have been right.
        plugins = _plugins()
        mine = {ending: plugin for ending, plugin in plugins.items()
                if open_to(plugin, user)}
        closed = _("Making files here is not included in your plan.")
        if plugins and not mine:
            return _refuse(closed, 402, reason="subscription-required")

        name = request.POST.get("name", "")
        refusal = name_refusal(name)
        if refusal:
            return _refuse(refusal, 400)
        ending = ending_of(name, plugins)
        if not ending:
            if "." not in name:
                return _refuse(_("Give the name an extension, for example notes.md."), 400)
            return _refuse(unsupported_sentence(mine or plugins), 400)
        if ending not in mine:
            return _refuse(closed, 402, reason="subscription-required")
        plugin = mine[ending]
        file_type = plugin.file_type
        refusal = upload_refusal(name, file_type=file_type)
        if refusal:
            return _refuse(refusal, 400)

        target = _folder(user, request.POST.get("directory_id", "").strip())
        if target is None:
            return _refuse(_("Folder not found."), 404)
        bucket, directory = target
        # Kept to clearances the member does not hold: missing, its owner
        # included (the pessimistic rule, no owner bypass).
        from toto.socialhub.clearance_access import group_hidden

        if group_hidden(user, Bucket.objects.filter(pk=bucket.pk)):
            return _refuse(_("Folder not found."), 404)
        if bucket.is_being_deleted:
            from .models import closed_bucket_sentence

            return _refuse(closed_bucket_sentence(bucket), 409)
        if not bucket.is_local:
            # A blank file exists to be edited, and editors need local bytes.
            return _refuse(_("This bucket's storage is remote — files are "
                             "uploaded or transferred into it, not created "
                             "empty here."), 403)

        try:
            data = (plugin.new_file_content(name) or "").encode("utf-8")
        except Exception:  # noqa: BLE001 - a broken plugin makes no file
            logger.exception("The editor plugin could not start %s.", ending)
            return _refuse(unsupported_sentence(set(mine) - {ending}), 400)

        # The upload door's checks, before anything is written.
        from decimal import Decimal

        from toto.quota import InArrears, QuotaExceeded, check_quota, record_usage
        from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for

        from .models import VaultQuotaPolicy, VaultUsageEvent
        from .storage_backends import UploadRefused, persist_upload

        size_mb = Decimal(str(len(data))) / Decimal("1048576")
        tariff = price_for(user, "vault")
        try:
            check_quota(VaultQuotaPolicy, "storage.request", 1, user)
            check_quota(VaultQuotaPolicy, "storage.transfer_mb", size_mb, user)
            check_funds(user, tariff, "storage.request", 1)
            check_funds(user, tariff, "storage.transfer_mb", size_mb)
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            return _refuse(exc, exc.status_code)

        try:
            with transaction.atomic():
                # One New at a time in a bucket, so two requests for one name
                # cannot both find the folder free.
                Bucket.objects.select_for_update().filter(pk=bucket.pk).first()
                if VaultFile.objects.filter(bucket=bucket, directory=directory,
                                            title__iexact=name).exists():
                    return _refuse(
                        _("A file named %(name)s is already in this folder.")
                        % {"name": name}, 409)
                # Keys address a file across the whole owner: unique per
                # owner, as the upload door keeps them.
                base_key = slugify(name) or "file"
                key, counter = base_key, 1
                while VaultFile.objects.filter(owner=user, key=key).exists():
                    key = f"{base_key}-{counter}"
                    counter += 1
                vault_file = VaultFile(
                    owner=user, title=name, key=key, file_type=file_type,
                    content_hash=hashlib.sha256(data).hexdigest(),
                    file_size_bytes=len(data), bucket=bucket, directory=directory)
                persist_upload(vault_file, ContentFile(data, name=name))
        except UploadRefused as exc:
            return _refuse(exc, 400)

        src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk)}
        record_usage(VaultUsageEvent, "storage.request", 1, user,
                     idempotency_key=f"vault.new_file.request:{vault_file.pk}", **src)
        charge(user, tariff, "storage.request", 1, **src)
        if size_mb > 0:
            record_usage(VaultUsageEvent, "storage.transfer_mb", size_mb, user,
                         unit="MB",
                         idempotency_key=f"vault.new_file.transfer:{vault_file.pk}", **src)
            charge(user, tariff, "storage.transfer_mb", size_mb, unit="MB", **src)
        _audit(request, vault_file)

        response = JsonResponse({
            "status": "ok",
            "file_pk": vault_file.pk,
            "title": vault_file.title,
            "file_type": vault_file.file_type,
        }, status=201)
        response["Cache-Control"] = "no-store"
        return response
