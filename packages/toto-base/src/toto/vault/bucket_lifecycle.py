"""A bucket's life after Create: its audit trail, Edit, and Delete.

Creating is the adapters' (``storage_adapters``); everything a bucket goes
through afterwards is the same for every kind and lives here, so Storage →
Management's views stay thin and the rules cannot drift between them.

## The audit chain

| action | when |
|---|---|
| ``VAULT.BUCKET.CREATED`` | a bucket is made in Management (kind, owner, target) |
| ``VAULT.BUCKET.UPDATED`` | Edit changed its name, owner, quota or AI shield (each field before and after) |
| ``VAULT.BUCKET.DELETE_REQUESTED`` | a superuser confirmed Delete (the files and bytes about to go) |
| ``VAULT.BUCKET.DELETE_FAILED`` | the purge stopped (``success=False``, the reason) |
| ``VAULT.BUCKET.DELETED`` | the purge finished: files, bucket (and a mount's pairing) gone |
| ``VAULT.BUCKET.SHARED`` | a share with another Zenobia was made (``share_views``: label, rights, end date) |
| ``VAULT.BUCKET.SHARE_ROTATED`` | a share got a new key (and end date) |
| ``VAULT.BUCKET.SHARE_REVOKED`` | a share was revoked |
| ``VAULT.BUCKET.PEER_KEY_REPLACED`` | a connected bucket's stored key was replaced by a rotated code |

Metadata names the bucket and what happened, never a secret: no key, no
token, no pairing code, no ciphertext — ``snapshot`` is the only shape a
bucket is recorded in, and it holds none (the chain's own sanitiser redacts
by key name as a second belt).

## Delete

``request_deletion`` (the typed name must match) marks the bucket
(``deletion_requested_at``) — from then on nothing new lands in it
(``VaultFile.save`` / ``persist_upload``), Edit refuses, and lists show it as
being deleted — and hands the purge to a worker after the commit.
``purge_bucket`` then takes every file:

- this server's and S3 buckets: ``purge.purge_file(strict=True)`` per file —
  the row, its bytes / its object and the bodies of its saved versions,
  through ONE driver built for the bucket (so its sealed S3 key is opened
  once, for this job only). Strict: bytes that cannot be deleted (a key
  without delete permission, a deactivated key, an unwritable disk) keep the
  row and count as a failure — a bucket is never recorded as deleted while
  its objects are still at the provider;
- a bucket connected from another Zenobia: its listing rows are deleted AS
  ROWS — never ``purge_file``, which would send a DELETE to the other host;

then the bucket itself (its folders, gateways, clearance keeping, grants and
sealed secret go with it), and, for a mount, the pairing when no other bucket
uses it. A file another app still holds (a PROTECT foreign key), or bytes that
would not go, stop the purge before the bucket goes: the bucket stays marked,
``deletion_error`` says why, and Delete may be confirmed again once the cause
is gone. Something ELSE that holds the bucket itself through a PROTECT
foreign key — a wiki topic keeping its pages' files here (2026-10-01) —
refuses Delete before anything is marked, and stops a purge before its first
file (``holders``): the purge would take every file and then fail on the
bucket. No file ever ends with ``bucket=None`` — ``VaultFile.bucket`` is
PROTECT — so no file ever falls out of its bucket's clearance keeping.

A purge never dies silently. The worker's soft time limit is never swallowed
as "one failed file"; one run works for at most ``PURGE_BUDGET_SECONDS`` and
then queues the next (``tasks.purge_bucket_task``), so none runs near the
hard limit; any error escaping the job is stamped on ``deletion_error`` and
audited. What no code can catch — a worker killed, a restart mid-purge — is
what ``storage_adapters.STATUS_DELETE_STALLED`` is for: a bucket still being
deleted long after Delete was last confirmed offers Delete again, which
resumes where the job stopped (the purge is idempotent).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Bucket, FileOrigin, StorageBackend, VaultFile

try:  # celery is optional in toto-base; without it nothing raises this
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover
    from .storage_backends import SoftTimeLimitExceeded

log = logging.getLogger("toto.vault")

APP_LABEL = "vault"
OBJECT_TYPE = "vault.bucket"

#: What Edit may change. Everything else — backend, provider, endpoint,
#: region, remote bucket, prefix, peer, slug, creator — is fixed at Create.
EDITABLE = ("name", "owner", "storage_quota_mb", "ai_protected")

#: Files handled per query while purging.
PURGE_CHUNK = 200
#: How long one worker run purges before it hands the rest to the next run —
#: well under the task's soft time limit (1500 s) and the host's hard one.
PURGE_BUDGET_SECONDS = 600
#: Failed files after which a run stops trying the rest: the cause (a key
#: without delete permission, a dead endpoint) is almost always the same, and
#: thirty thousand refused calls say nothing the first twenty did not.
PURGE_MAX_FAILURES = 20


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def snapshot(bucket) -> dict:
    """The bucket as the chain records it — never a secret."""
    from .storage_adapters import StorageAdapter

    adapter = StorageAdapter.for_bucket(bucket)
    return {
        "name": bucket.name,
        "slug": bucket.slug,
        "owner": bucket.owner.get_username() if bucket.owner_id else None,
        "storage_quota_mb": bucket.storage_quota_mb,
        "ai_protected": bool(bucket.ai_protected),
        "kind": adapter.get_key() if adapter else bucket.storage_backend,
        "backend": bucket.storage_backend or "local",
        "target": adapter.target(bucket) if adapter else "",
    }


def audit(action, bucket, actor, *, success=True, object_id=None, **metadata):
    """One record on the chain (``VAULT.BUCKET.<ACTION>``); never breaks the
    act it describes."""
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.services import SYSTEM, record

    try:
        with transaction.atomic():
            return record(
                f"vault.bucket.{action}", app_label=APP_LABEL, object_type=OBJECT_TYPE,
                object_id=str(object_id if object_id is not None else bucket.pk),
                description=bucket.name, actor_user=actor if actor is not None else SYSTEM,
                success=success, metadata=metadata)
    except Exception:  # noqa: BLE001 - the chain never breaks a bucket change
        log.exception("audit: could not record vault.bucket.%s", action)
        return None


def record_created(bucket, actor, *, adapter=None):
    return audit("created", bucket, actor, **snapshot(bucket),
                 created_by=actor.get_username() if actor is not None else None)


def record_updated(bucket, actor, before: dict, after: dict):
    changed = {k: {"before": before.get(k), "after": after.get(k)}
               for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
    if not changed:
        return None
    return audit("updated", bucket, actor, slug=bucket.slug, changes=changed)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

def _same_quota(raw, stored) -> bool:
    """Whether a posted quota is the one the bucket already has (blank is
    "unlimited", i.e. None)."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return stored is None
    try:
        return stored is not None and int(text) == stored
    except ValueError:
        return False


def _editable_view(bucket) -> dict:
    return {
        "name": bucket.name,
        "owner": bucket.owner.get_username() if bucket.owner_id else None,
        "storage_quota_mb": bucket.storage_quota_mb,
        "ai_protected": bool(bucket.ai_protected),
    }


def update_bucket(bucket, actor, **changes):
    """Edit: name, owner, quota, AI shield — nothing that moves data.

    Returns the list of fields that changed. Refuses, with a sentence, any
    other field, an owner who is not an active account, a taken name, and a
    bucket that is being deleted.

    A field is checked only when its value CHANGES. The modal always posts
    all four, and what a bucket already has is not up for review: a legacy
    twin name differing only in case, an owner whose account was deactivated
    since, a quota of 0 from the admin — none of them blocks changing
    something else.
    """
    from .storage_adapters import clean_name, clean_owner, clean_quota

    fixed = sorted(set(changes) - set(EDITABLE))
    if fixed:
        raise ValidationError(_("%(fields)s cannot be changed after the bucket is created.")
                              % {"fields": ", ".join(fixed)})
    given, bucket = bucket, Bucket.objects.select_related("owner").get(pk=bucket.pk)
    if bucket.is_being_deleted:
        raise ValidationError(_("This bucket is being deleted and can no longer be edited."))
    before = _editable_view(bucket)
    if "name" in changes and str(changes["name"] or "").strip() != bucket.name:
        bucket.name = clean_name(changes["name"], exclude_pk=bucket.pk)
    if "owner" in changes and getattr(changes["owner"], "pk", None) != bucket.owner_id:
        bucket.owner = clean_owner(changes["owner"])
    if "storage_quota_mb" in changes and not _same_quota(changes["storage_quota_mb"],
                                                         bucket.storage_quota_mb):
        bucket.storage_quota_mb = clean_quota(changes["storage_quota_mb"])
    if "ai_protected" in changes:
        bucket.ai_protected = bool(changes["ai_protected"])
    after = _editable_view(bucket)
    changed = [k for k in EDITABLE if before[k] != after[k]]
    if not changed:
        return []
    with transaction.atomic():
        bucket.save(update_fields=changed)
        record_updated(bucket, actor, before, after)
    for name in changed:
        setattr(given, name, getattr(bucket, name))
    return changed


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def holders(bucket) -> list:
    """What holds ``bucket`` besides its files (2026-10-01): for each other
    model whose foreign key to it is PROTECT and has rows naming it, its
    plural name and up to five of the rows (``"wiki topics: Payroll, Board"``).
    A wiki topic keeps its pages' files in a bucket this way. Files are the
    purge's own to take; these it cannot, so Delete refuses while any is left
    rather than taking every file and then failing on the bucket."""
    from django.db.models import PROTECT

    out = []
    for relation in Bucket._meta.related_objects:
        if relation.on_delete is not PROTECT or relation.related_model is VaultFile:
            continue
        model = relation.related_model
        rows = list(model._base_manager.filter(**{relation.field.name: bucket})[:6])
        if rows:
            names = ", ".join(str(row) for row in rows[:5]) + ("…" if len(rows) > 5 else "")
            out.append(f"{model._meta.verbose_name_plural}: {names}")
    return out


def holders_sentence(held) -> str:
    return _("Other parts of Zenobia still keep their files in this bucket (%(holders)s). "
             "Give them another bucket first, then delete this one.") % {
        "holders": "; ".join(held)}


def request_deletion(bucket, actor, *, confirm_name: str):
    """Confirm Delete: check the typed name, mark the bucket, audit, and hand
    the purge to a worker once this transaction commits. Returns the bucket.

    Confirming again (a purge that stopped, a worker that died) re-dispatches;
    the purge resumes where the last one stopped. ``deletion_requested_at`` is
    the LAST confirmation — the clock ``storage_adapters.purge_stalled`` reads.
    """
    from .storage_adapters import StorageAdapter

    bucket = Bucket.objects.get(pk=bucket.pk)
    if str(confirm_name or "").strip() != bucket.name:
        raise ValidationError({"confirm_name": _("Type the bucket's name exactly to confirm.")})
    held = holders(bucket)
    if held:
        raise ValidationError(holders_sentence(held))
    adapter = StorageAdapter.for_bucket(bucket)
    plan = adapter.destroy_plan(bucket) if adapter else {"files": 0, "bytes": 0}
    with transaction.atomic():
        Bucket.objects.filter(pk=bucket.pk).update(
            deletion_requested_at=timezone.now(), deletion_error="")
        bucket.refresh_from_db()
        audit("delete_requested", bucket, actor, **snapshot(bucket),
              files=plan.get("files", 0), bytes=plan.get("bytes", 0))
        pk, actor_pk = bucket.pk, getattr(actor, "pk", None)
        transaction.on_commit(lambda: dispatch_purge(pk, actor_pk=actor_pk))
    return bucket


def dispatch_purge(bucket_pk: int, *, actor_pk=None):
    """Hand the purge to a worker; stamp a sentence when there is none.

    ``VAULT_PURGE_INLINE = True`` runs it in this process instead — the
    tests, and a development server with no worker. Production never needs it:
    a purge of a large or remote bucket is not a web request's job.
    """
    if getattr(settings, "VAULT_PURGE_INLINE", False):
        return purge_bucket(bucket_pk, actor_pk=actor_pk)
    if queue_purge(bucket_pk, actor_pk=actor_pk):
        return None
    Bucket.objects.filter(pk=bucket_pk).update(deletion_error=_(
        "No worker is running, so nothing has been deleted yet. Start one, "
        "then confirm Delete again."))
    return None


def queue_purge(bucket_pk: int, *, actor_pk=None) -> bool:
    """Queue one purge run on a worker. False when no worker (or broker) is
    there to take it."""
    try:
        from toto.celery_utils import celery_available

        if celery_available():
            from .tasks import purge_bucket_task

            purge_bucket_task.delay(bucket_pk, actor_pk)
            return True
    except Exception:  # noqa: BLE001 - a broker that is down is "no worker"
        log.exception("vault: could not queue the purge of bucket %s", bucket_pk)
    return False


def _stop(bucket, actor, reason: str, **facts) -> dict:
    Bucket.objects.filter(pk=bucket.pk).update(deletion_error=reason[:2000])
    audit("delete_failed", bucket, actor, success=False, slug=bucket.slug, reason=reason[:500],
          **facts)
    return {"ok": False, "error": reason, **facts}


def stop_purge(bucket_pk: int, reason: str, *, actor_pk=None) -> dict:
    """Stamp why a purge job stopped (the task's own catch-all: a time limit,
    an error that escaped ``purge_bucket``) and audit it — so the bucket reads
    "Deletion stopped" and offers Delete again, never "being deleted" forever.
    Never raises."""
    try:
        bucket = Bucket.objects.filter(pk=bucket_pk).first()
        if bucket is None or not bucket.is_being_deleted:
            return {"ok": bucket is None, "gone": bucket is None}
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=actor_pk).first() if actor_pk else None
        return _stop(bucket, actor, reason)
    except Exception:  # noqa: BLE001 - a dying worker; the stall offer is the backstop
        log.exception("vault: could not record why the purge of bucket %s stopped", bucket_pk)
        return {"ok": False, "error": reason}


def purge_bucket(bucket_pk: int, *, actor_pk=None, budget: float | None = None) -> dict:
    """The worker's body: every file, then the bucket. Idempotent — a bucket
    already gone is done, and a stopped purge resumes where it stopped.

    Refuses a bucket nobody asked to delete: the mark is the authorisation.

    ``budget`` (seconds) bounds one run: past it, the run stops between two
    files and answers ``{"ok": False, "more": True}`` for its caller to queue
    the next (``tasks.purge_bucket_task``). A worker's soft time limit is
    re-raised, never counted as one failed file.
    """
    import time

    from .purge import purge_file
    from .storage_backends import get_bucket_storage

    started = time.monotonic()
    bucket = Bucket.objects.select_related("peer", "provider", "owner").filter(pk=bucket_pk).first()
    if bucket is None:
        return {"ok": True, "gone": True}
    if not bucket.is_being_deleted:
        return {"ok": False, "error": "Deletion was not requested for this bucket."}
    from django.contrib.auth import get_user_model

    # Who confirmed Delete — the records of the job are theirs.
    actor = get_user_model().objects.filter(pk=actor_pk).first() if actor_pk else None
    held = holders(bucket)
    if held:
        # Before the first file: something that came to hold the bucket since
        # Delete was confirmed keeps every file, and the mark says why.
        return _stop(bucket, actor, holders_sentence(held))

    remote = bucket.storage_backend == StorageBackend.REMOTE_TOTO
    driver = None
    if not remote:
        try:
            driver = get_bucket_storage(bucket)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - external buckets off, unreadable secret
            return _stop(bucket, actor, _("The bucket's storage cannot be reached: %(reason)s")
                         % {"reason": exc})

    deleted, held, failed, failures, handled, out_of_time = 0, [], "", 0, 0, False
    # all_objects (2026-10-01): the trash goes with its bucket too — a
    # trashed file is still in it, and PROTECT would hold the bucket.
    pks = list(VaultFile.all_objects.filter(bucket_id=bucket.pk).order_by("pk")
               .values_list("pk", flat=True))
    for start in range(0, len(pks), PURGE_CHUNK):
        if failures >= PURGE_MAX_FAILURES or out_of_time:
            break
        chunk = VaultFile.all_objects.filter(pk__in=pks[start:start + PURGE_CHUNK]).select_related("bucket")
        for vault_file in chunk:
            if budget is not None and handled and time.monotonic() - started >= budget:
                out_of_time = True
                break
            handled += 1
            file_pk = vault_file.pk         # delete() clears it, even when rolled back
            try:
                if remote or vault_file.origin == FileOrigin.MIRROR:
                    # A listing row of the other host's file: a ROW delete.
                    # purge_file would ask the driver to delete the object —
                    # a DELETE on the other Zenobia.
                    with transaction.atomic():
                        vault_file.delete()
                else:
                    # Strict: bytes that will not go keep the row, and raise.
                    purge_file(vault_file, driver=driver, strict=True)
                deleted += 1
            except SoftTimeLimitExceeded:
                raise                   # the worker's clock, never "one failed file"
            except (ProtectedError, RestrictedError):
                held.append(vault_file.title)
            except Exception as exc:  # noqa: BLE001 - one file must not hide the rest
                log.exception("vault: purge of file %s in bucket %s failed", file_pk, bucket.pk)
                failed = failed or str(exc)[:300]
                failures += 1
                if failures >= PURGE_MAX_FAILURES:
                    break

    if out_of_time and not failed:
        # Between two files (and never before the first — every run moves
        # forward): what is done is gone for good, the rest is the next run's.
        # A run that also met failures stops instead: the next would meet
        # them first again, and could spend its whole time there.
        return {"ok": False, "more": True, "files_deleted": deleted}
    if failed and not held:
        left = VaultFile.all_objects.filter(bucket_id=bucket.pk).count()
        return _stop(bucket, actor, _(
            "%(count)s file(s) could not be deleted: %(reason)s. Confirm Delete again "
            "once that is fixed.") % {"count": left, "reason": failed},
            files_deleted=deleted, files_left=left)
    if held or VaultFile.all_objects.filter(bucket_id=bucket.pk).exists():
        left = VaultFile.all_objects.filter(bucket_id=bucket.pk).count()
        names = ", ".join(held[:5]) + ("…" if len(held) > 5 else "")
        return _stop(bucket, actor, _(
            "%(count)s file(s) could not be deleted because another part of Zenobia "
            "still uses them%(names)s. Remove that use, then confirm Delete again.")
            % {"count": left, "names": f" ({names})" if names else ""},
            files_deleted=deleted, files_left=left)

    facts = snapshot(bucket)
    object_id = bucket.pk
    peer = bucket.peer if (remote and bucket.peer_id) else None
    try:
        with transaction.atomic():
            if not Bucket.objects.filter(pk=object_id).exists():
                # A second run (a confirmation while this one worked) got here
                # first: the bucket and its record are its.
                return {"ok": True, "gone": True, "files_deleted": deleted}
            bucket.delete()
            peer_removed = False
            if peer is not None and not Bucket.objects.filter(peer_id=peer.pk).exists():
                peer.delete()
                peer_removed = True
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - ProtectedError and the rest: say why, keep the mark
        bucket.pk = object_id
        return _stop(bucket, actor, _("The bucket could not be deleted: %(reason)s")
                     % {"reason": exc}, files_deleted=deleted)
    bucket.pk = object_id
    audit("deleted", bucket, actor, object_id=object_id, **facts, files_deleted=deleted,
          pairing_removed=peer_removed)
    return {"ok": True, "files_deleted": deleted, "pairing_removed": peer_removed}
