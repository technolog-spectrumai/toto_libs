"""What makes a notification (2026-10-04).

Every source is a signal an app already sends, listened to here — so every
writer is covered (a page, the JSON API, the admin, a worker, the console)
and no door of the vault or the socialhub had to learn about notifications.
``connect`` attaches each one only where its app is installed.

| told | when | who is told |
|---|---|---|
| a file uploaded, replaced, moved to the trash, restored | ``vault.signals.file_changed`` | the bucket's owner, the file's owner, the people on the folder's access list — each only if they may read the file |
| access to a folder | somebody is added to a folder's access list | the person added |
| a bucket set up for you | a bucket is made with you as its owner by somebody else | the owner |
| a clearance given, taken away | ``Person.clearances`` changes (or the clearance is deleted) | the person |
| a transfer, a zip, a paired host's refresh finished or failed | the run's row reaches its end | who started it |
| the copy of your data ready or failed; an erasure request declined | the row's status | the member |
| a new sign-in, a changed password or e-mail address | ``toto.core.notices.send_notice`` (it calls ``account_notice`` below) | the member |

**Never more than the recipient may already see.** A file's name goes only
to somebody ``vault.access.may_read`` lets read it — for a file in a bucket
kept to clearances that is its holders alone, not the bucket's owner and not
the file's (pessimistic, no owner bypass). A bucket's name goes only to
somebody it is not hidden from. A share made of a bucket WITH ANOTHER
ZENOBIA is told to nobody: its list is the administrators', and the bucket's
owner cannot see it.

**Not to the one who did it.** Who did it is whoever is at the keyboard for
the request being served (the audit context, where ``toto.audit`` runs);
with no request — a worker, the console — an upload's is the file's owner
and a trashing's is ``trashed_by``.

Nothing here raises into the change it reports: ``services.send`` swallows,
and each receiver is wrapped the same way.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import wraps

from django.apps import apps
from django.utils import timezone

from . import kinds
from .services import send

log = logging.getLogger("toto.notify")

#: A job that ended longer ago than this is not news (a row saved again).
JOB_NEWS_WINDOW = timedelta(days=1)


def quiet(receiver):
    """A receiver that never fails the signal's sender."""

    @wraps(receiver)
    def wrapped(*args, **kwargs):
        try:
            return receiver(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            log.warning("notify: %s failed (%s)", receiver.__name__, type(exc).__name__)
            return None

    return wrapped


def actor():
    """Whoever is at the keyboard for the request being served, or ``None``."""
    try:
        from toto.audit.context import current_context
    except ImportError:
        return None
    user = getattr(current_context(), "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _url(name, *args, query: str = "") -> str:
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse(name, args=args) + query
    except NoReverseMatch:
        return ""


def _bucket_link(bucket) -> str:
    from urllib.parse import quote

    return _url("vault:public_list", query="?bucket=" + quote(bucket.slug or ""))


def _recent(moment) -> bool:
    return moment is not None and timezone.now() - moment <= JOB_NEWS_WINDOW


# ---------------------------------------------------------------------------
# Files in my buckets
# ---------------------------------------------------------------------------

FILE_KINDS = {
    "uploaded": kinds.VAULT_UPLOADED,
    "replaced": kinds.VAULT_REPLACED,
    "trashed": kinds.VAULT_TRASHED,
    "restored": kinds.VAULT_RESTORED,
}


def file_recipients(vault_file, directory_id, *, less=None) -> list:
    """Who is told about a file in ``directory_id`` of its bucket: the
    bucket's owner, the file's owner and the folder's access list, each only
    if ``vault.access.may_read`` lets them read it THERE — and never
    ``less``, the one who did it."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ObjectDoesNotExist

    from toto.vault import access
    from toto.vault.models import Bucket, VaultDirectory, VaultFile

    candidates = {vault_file.owner_id}
    candidates.add(Bucket.objects.filter(pk=vault_file.bucket_id)
                   .values_list("owner_id", flat=True).first())
    if directory_id:
        candidates |= set(VaultDirectory.allowed_users.through.objects
                          .filter(vaultdirectory_id=directory_id)
                          .values_list("user_id", flat=True))
    candidates.discard(None)
    if less is not None:
        candidates.discard(less.pk)
    if not candidates:
        return []
    # The file as it stands in that folder: a trashed row has left its
    # folder, and the question is who could read it where it was.
    view = VaultFile(pk=vault_file.pk, owner_id=vault_file.owner_id,
                     is_public=vault_file.is_public, bucket_id=vault_file.bucket_id,
                     directory_id=directory_id or None)
    told = []
    for user in get_user_model()._default_manager.filter(pk__in=candidates, is_active=True):
        try:
            if access.may_read(user, view):
                told.append(user)
        except ObjectDoesNotExist:
            continue
    return told


@quiet
def on_file_changed(sender=None, file=None, kind="", was=None, **kwargs):
    told_as = FILE_KINDS.get(kind)
    if told_as is None or file is None or not file.bucket_id:
        return
    if getattr(file, "origin", "") == "mirror":
        return          # a paired host's listing, not somebody's act here
    was = was or {}
    acted = actor()
    if acted is None and kind == "uploaded":
        acted = file.owner
    if acted is None and kind == "trashed":
        acted = file.trashed_by
    directory_id = was.get("directory_id") if kind == "trashed" else file.directory_id
    recipients = file_recipients(file, directory_id, less=acted)
    if not recipients:
        return
    bucket = file.bucket
    link = _bucket_link(bucket)
    for user in recipients:
        send(user, told_as.key, actor=acted, collapse=f"{kind}:{bucket.pk}", link=link,
             title=file.title, bucket=bucket.name, bucket_id=bucket.pk, file_id=file.pk)


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------

@quiet
def on_folder_access(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Somebody was added to a folder's access list."""
    if action != "post_add" or not pk_set:
        return
    from django.contrib.auth import get_user_model

    from toto.socialhub.clearance_access import group_hidden
    from toto.vault.models import Bucket, VaultDirectory

    if reverse:         # instance is the account; the pks are folders
        pairs = [(instance, directory) for directory in
                 VaultDirectory.objects.filter(pk__in=pk_set).select_related("bucket")]
    else:               # instance is the folder; the pks are accounts
        pairs = [(user, instance) for user in
                 get_user_model()._default_manager.filter(pk__in=pk_set)]
    acted = actor()
    for user, directory in pairs:
        bucket = directory.bucket
        # Theirs already: being named on the list of one's own folder is no news.
        if user.pk in (directory.owner_id, bucket.owner_id):
            continue
        if group_hidden(user, Bucket.objects.filter(pk=bucket.pk)):
            continue
        send(user, kinds.FOLDER_SHARED.key, actor=acted, link=_bucket_link(bucket),
             folder=directory.name, bucket=bucket.name, bucket_id=bucket.pk)


@quiet
def on_bucket_saved(sender, instance, created, raw=False, **kwargs):
    """A bucket made for somebody by somebody else (a connected bucket of
    another Zenobia with its owner chosen, one made in the admin)."""
    if raw or not created or not instance.owner_id:
        return
    acted = actor()
    if acted is None or acted.pk == instance.owner_id:
        return
    send(instance.owner, kinds.BUCKET_GIVEN.key, actor=acted, link=_bucket_link(instance),
         bucket=instance.name, bucket_id=instance.pk)


# ---------------------------------------------------------------------------
# Clearances
# ---------------------------------------------------------------------------

def _tell_clearance(person, clearance, kind, acted):
    user = getattr(person, "user", None)
    if user is None:
        return
    send(user, kind.key, actor=acted, link=_url("account:home"), clearance=clearance.name)


@quiet
def on_clearances(sender, instance, action, reverse, model, pk_set, **kwargs):
    """``Person.clearances``, from either side, a ``clear()`` included —
    ``add`` reports only the rows it made, ``remove`` whatever it was
    passed, so who really loses one is read before it goes."""
    from toto.people.models import Person
    from toto.socialhub.models import Clearance

    if action in ("pre_remove", "pre_clear"):
        manager = instance.members if reverse else instance.clearances
        current = set(manager.values_list("pk", flat=True))
        instance._notify_leaving = (current & set(pk_set or ())
                                    if action == "pre_remove" else current)
        return
    if action == "post_add":
        pks, kind = set(pk_set or ()), kinds.CLEARANCE_GRANTED
    elif action in ("post_remove", "post_clear"):
        pks, kind = getattr(instance, "_notify_leaving", set()), kinds.CLEARANCE_REMOVED
        instance._notify_leaving = set()
    else:
        return
    if not pks:
        return
    acted = actor()
    if reverse:         # instance is the clearance; the pks are people
        for person in Person.objects.filter(pk__in=pks).select_related("user"):
            _tell_clearance(person, instance, kind, acted)
    else:               # instance is the person; the pks are clearances
        for clearance in Clearance.objects.filter(pk__in=pks):
            _tell_clearance(instance, clearance, kind, acted)


@quiet
def on_clearance_leaving(sender, instance, **kwargs):
    # The delete cascades Person.clearances rows without m2m_changed, so the
    # holders are read here, before they go, and told after.
    instance._notify_holders = list(instance.members.select_related("user"))


@quiet
def on_clearance_deleted(sender, instance, **kwargs):
    holders = getattr(instance, "_notify_holders", None) or []
    instance._notify_holders = None
    acted = actor()
    for person in holders:
        _tell_clearance(person, instance, kinds.CLEARANCE_REMOVED, acted)


# ---------------------------------------------------------------------------
# Transfers and background jobs
# ---------------------------------------------------------------------------

@quiet
def on_transfer_saved(sender, instance, raw=False, **kwargs):
    if raw or not instance.is_finished or not instance.owner_id:
        return
    if not _recent(instance.finished_at):
        return
    failed = instance.status == "failed"
    bucket = instance.dest_bucket if instance.dest_bucket_id else None
    send(instance.owner, (kinds.TRANSFER_FAILED if failed else kinds.TRANSFER_DONE).key,
         once=f"transfer:{instance.pk}", link=_url("vault:transfer_detail", instance.pk),
         bucket=bucket.name if bucket is not None else "")


@quiet
def on_refresh_saved(sender, instance, raw=False, **kwargs):
    if raw or not instance.is_finished or not instance.owner_id:
        return
    if not _recent(instance.finished_at):
        return
    failed = instance.status == "failed"
    bucket = instance.bucket
    send(instance.owner, (kinds.REFRESH_FAILED if failed else kinds.REFRESH_DONE).key,
         once=f"refresh:{instance.pk}", link=_bucket_link(bucket), bucket=bucket.name)


#: The workflow a zip runs as (``vault.views.CreateZipView``).
ZIP_WORKFLOW = "vault-zip"


@quiet
def on_workflow_run_saved(sender, instance, raw=False, **kwargs):
    if raw or instance.status not in (sender.COMPLETED, sender.FAILED):
        return
    if not instance.started_by_id or instance.workflow.slug != ZIP_WORKFLOW:
        return
    failed = instance.status == sender.FAILED
    send(instance.started_by, (kinds.ZIP_FAILED if failed else kinds.ZIP_DONE).key,
         once=f"zip:{instance.pk}", link=_url("vault:archive"))


# ---------------------------------------------------------------------------
# Account, security and privacy
# ---------------------------------------------------------------------------

def account_notice(user, notice_kind: str) -> None:
    """A mailed notice (``toto.core.notices``), in the bell too. Called by
    ``send_notice`` for the kinds in ``kinds.NOTICE_KINDS``; never raises."""
    kind = kinds.NOTICE_KINDS.get(notice_kind)
    if kind is None or user is None:
        return
    send(user, kind, link=_url("account:home"))


@quiet
def on_data_export_saved(sender, instance, raw=False, **kwargs):
    if raw or instance.status not in (sender.READY, sender.FAILED):
        return
    if not _recent(instance.finished_at):
        return
    failed = instance.status == sender.FAILED
    send(instance.user, (kinds.EXPORT_FAILED if failed else kinds.EXPORT_READY).key,
         once=f"export:{instance.pk}", link=_url("account:home"))


@quiet
def on_erasure_request_saved(sender, instance, raw=False, **kwargs):
    if raw or instance.status != sender.DECLINED or not instance.user_id:
        return
    if not _recent(instance.handled_at):
        return
    # No actor: who declined it is the operators' side of the ticket.
    send(instance.user, kinds.ERASURE_DECLINED.key,
         once=f"erasure:{instance.pk}", link=_url("account:home"))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def connect() -> None:
    from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete

    uid = "toto.notify.sources."
    if apps.is_installed("toto.vault"):
        from toto.vault.mirror import BucketRefreshRun
        from toto.vault.models import Bucket, VaultDirectory
        from toto.vault.signals import file_changed
        from toto.vault.transfer import TransferRun

        file_changed.connect(on_file_changed, dispatch_uid=uid + "file_changed")
        m2m_changed.connect(on_folder_access, sender=VaultDirectory.allowed_users.through,
                            dispatch_uid=uid + "folder_access")
        post_save.connect(on_bucket_saved, sender=Bucket, dispatch_uid=uid + "bucket")
        post_save.connect(on_transfer_saved, sender=TransferRun, dispatch_uid=uid + "transfer")
        post_save.connect(on_refresh_saved, sender=BucketRefreshRun,
                          dispatch_uid=uid + "refresh")
    if apps.is_installed("toto.people") and apps.is_installed("toto.socialhub"):
        from toto.people.models import Person
        from toto.socialhub.models import Clearance, DataExport, ErasureRequest

        m2m_changed.connect(on_clearances, sender=Person.clearances.through,
                            dispatch_uid=uid + "clearances")
        pre_delete.connect(on_clearance_leaving, sender=Clearance,
                           dispatch_uid=uid + "clearance_leaving")
        post_delete.connect(on_clearance_deleted, sender=Clearance,
                            dispatch_uid=uid + "clearance_deleted")
        post_save.connect(on_data_export_saved, sender=DataExport,
                          dispatch_uid=uid + "data_export")
        post_save.connect(on_erasure_request_saved, sender=ErasureRequest,
                          dispatch_uid=uid + "erasure")
    if apps.is_installed("toto.workflows"):
        from toto.workflows.models import WorkflowRun

        post_save.connect(on_workflow_run_saved, sender=WorkflowRun,
                          dispatch_uid=uid + "workflow_run")
