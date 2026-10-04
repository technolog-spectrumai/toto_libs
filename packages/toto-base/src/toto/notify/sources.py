"""What makes a notification (2026-10-04).

One source: the vault's ``file_changed`` signal, listened to here — so every
writer is covered (a page, the JSON API, the admin, a worker, the console)
and no door of the vault had to learn about notifications.

**Only about a bucket you own.** A file uploaded, replaced, moved to the
trash or restored in a bucket tells that bucket's OWNER, and nobody else:
not the file's owner, not the people on a folder's access list. A rename, a
move, a lock and a delete outright tell nobody.

**Never more than the owner may already see.** The owner is told only if
``vault.access.may_read`` lets them read the file where it is — for a bucket
kept to clearances that is its holders alone, so an owner who holds none of
them is told nothing (pessimistic, no owner bypass).

**Not to the one who did it.** Who did it is whoever is at the keyboard for
the request being served (the audit context, where ``toto.audit`` runs);
with no request — a worker, the console — an upload's is the file's owner
and a trashing's is ``trashed_by``.

What is NOT a source since 2026-10-04 (the owner: "notifications (but only
for your buckets)"): folder access lists and buckets set up for somebody,
clearances, finished transfers, refreshes and archives, the copy of one's
data, a declined erasure, the mailed account notices — and anybody signing
in, signing out or joining a community.

Nothing here raises into the change it reports: ``services.send`` swallows,
and the receiver is wrapped the same way.
"""

from __future__ import annotations

import logging
from functools import wraps

from django.apps import apps

from . import kinds
from .services import send

log = logging.getLogger("toto.notify")


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


def _bucket_link(bucket) -> str:
    from urllib.parse import quote

    from django.urls import NoReverseMatch, reverse

    try:
        return reverse("vault:public_list") + "?bucket=" + quote(bucket.slug or "")
    except NoReverseMatch:
        return ""


FILE_KINDS = {
    "uploaded": kinds.VAULT_UPLOADED,
    "replaced": kinds.VAULT_REPLACED,
    "trashed": kinds.VAULT_TRASHED,
    "restored": kinds.VAULT_RESTORED,
}


def bucket_owner(vault_file, directory_id, *, less=None):
    """The owner of the file's bucket, if they are to be told about it: an
    active account, not ``less`` (the one who did it), and one that
    ``vault.access.may_read`` lets read the file in ``directory_id`` of that
    bucket. Else ``None``."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ObjectDoesNotExist

    from toto.vault import access
    from toto.vault.models import Bucket, VaultFile

    owner_id = (Bucket.objects.filter(pk=vault_file.bucket_id)
                .values_list("owner_id", flat=True).first())
    if owner_id is None or (less is not None and less.pk == owner_id):
        return None
    owner = get_user_model()._default_manager.filter(pk=owner_id, is_active=True).first()
    if owner is None:
        return None
    # The file as it stands in that folder: a trashed row has left its
    # folder, and the question is whether they could read it where it was.
    view = VaultFile(pk=vault_file.pk, owner_id=vault_file.owner_id,
                     is_public=vault_file.is_public, bucket_id=vault_file.bucket_id,
                     directory_id=directory_id or None)
    try:
        return owner if access.may_read(owner, view) else None
    except ObjectDoesNotExist:
        return None


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
    owner = bucket_owner(file, directory_id, less=acted)
    if owner is None:
        return
    bucket = file.bucket
    send(owner, told_as.key, actor=acted, collapse=f"{kind}:{bucket.pk}",
         link=_bucket_link(bucket), title=file.title, bucket=bucket.name,
         bucket_id=bucket.pk, file_id=file.pk)


def connect() -> None:
    if not apps.is_installed("toto.vault"):
        return
    from toto.vault.signals import file_changed

    file_changed.connect(on_file_changed, dispatch_uid="toto.notify.sources.file_changed")
