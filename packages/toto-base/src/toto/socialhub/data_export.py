"""*Download my data* on My account (2026-10-01, RODO art. 15 and 20).

The texlab order, like the vault's transfers: :func:`request_export` writes
the ``DataExport`` row and queues the job, the worker runs :func:`build`,
:func:`fail` closes a row that will not finish. The zip is the console's
(``toto.core.personal_data.write_zip``), filed in the member's personal
bucket under a key naming the export, so a redelivered job finds its own
file instead of writing a second.

**Refuse, never inline.** An export walks every table and copies every file
the member owns; on a web worker that is a request that can take minutes. So
a build with no worker listening gets a sentence saying so, and no row.

**One open, one a day.** A second press while one is queued or being built
is refused (the row's constraint is the last word), and so is a new one
within :data:`INTERVAL` of the last that did not fail — a failed one does
not count, or a dead worker would lock the member out for a day. A row left
open longer than :data:`STALE` (a worker killed mid-build) is closed as
failed the next time the member looks, so it never blocks them for good.

On the chain: ``PRIVACY.EXPORT_REQUESTED`` (the member), ``_READY`` and
``_FAILED`` (the system) — counts and the file's id, never the contents.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.socialhub import audit
from toto.socialhub.models import DataExport

log = logging.getLogger("toto.socialhub")

#: One export a day per member.
INTERVAL = timedelta(days=1)
#: An open row older than this is a job that died.
STALE = timedelta(hours=6)


class Refused(Exception):
    """The export was not queued; the message says why, to the member."""


def latest_for(user):
    return DataExport.objects.filter(user=user).select_related("output", "output__bucket").first()


def close_stale(user) -> int:
    """Close the member's open rows that no worker will finish."""
    closed = 0
    for export in DataExport.objects.filter(user=user, status__in=DataExport.OPEN,
                                            created_at__lt=timezone.now() - STALE):
        fail(export, _("The export did not finish in time. Ask for a new one."))
        closed += 1
    return closed


def next_allowed_at(user):
    """When the member may ask again, or None for now."""
    last = (DataExport.objects.filter(user=user).exclude(status=DataExport.FAILED)
            .order_by("-created_at").first())
    if last is None or last.created_at + INTERVAL <= timezone.now():
        return None
    return last.created_at + INTERVAL


def worker_available() -> bool:
    from toto.celery_utils import celery_available

    return celery_available()


def request_export(user, *, request=None) -> DataExport:
    """Queue an export of ``user``'s data. Raises :class:`Refused`."""
    close_stale(user)
    if DataExport.objects.filter(user=user, status__in=DataExport.OPEN).exists():
        raise Refused(_("Your data is already being prepared. It appears below when it "
                        "is ready."))
    if next_allowed_at(user) is not None:
        raise Refused(_("You can ask for a copy of your data once a day. Your last one "
                        "is below."))
    if not worker_available():
        raise Refused(_("The platform's background worker is not running, so the copy "
                        "cannot be prepared now. Try again later, or tell an administrator."))
    try:
        with transaction.atomic():
            export = DataExport.objects.create(user=user)
    except IntegrityError:
        raise Refused(_("Your data is already being prepared. It appears below when it "
                        "is ready.")) from None
    audit.export_requested(export, request=request)

    from toto.socialhub.tasks import build_data_export

    try:
        result = build_data_export.delay(export.pk)
    except Exception:  # noqa: BLE001 - no broker: close the row, say so
        log.exception("data export %s: could not be queued", export.pk)
        fail(export, _("The export could not be queued. Try again later."))
        raise Refused(_("The export could not be queued. Try again later.")) from None
    export.task_id = getattr(result, "id", "") or ""
    export.save(update_fields=["task_id"])
    return export


def fail(export, reason: str) -> None:
    """Close a row that will never finish. Idempotent."""
    if not export.is_open:
        return
    export.status = DataExport.FAILED
    export.error = (reason or _("The export failed."))[:300]
    export.finished_at = timezone.now()
    export.save(update_fields=["status", "error", "finished_at"])
    audit.export_failed(export)


def _file_key(export) -> str:
    return f"data-export-{export.pk}"


def build(export_id: int):
    """The worker's half: write the zip into the member's personal bucket.
    Idempotent — a row that is not pending is left alone, and a file already
    filed under this export's key is taken rather than written again."""
    from django.core.files import File

    from toto.core.personal_data import write_zip
    from toto.vault.models import VaultFile, personal_bucket

    with transaction.atomic():
        export = (DataExport.objects.select_for_update().select_related("user")
                  .filter(pk=export_id).first())
        if export is None or export.status != DataExport.PENDING:
            return export
        export.status = DataExport.RUNNING
        export.started_at = timezone.now()
        export.save(update_fields=["status", "started_at"])
    user = export.user
    try:
        bucket = personal_bucket(user)
        key = _file_key(export)
        vault_file = VaultFile.objects.filter(bucket=bucket, key=key).first()
        summary = export.summary or {}
        if vault_file is None:
            stamp = timezone.now().strftime("%Y-%m-%d")
            name = f"my-data-{user.get_username()}-{stamp}.zip"
            with tempfile.TemporaryFile(suffix=".zip") as tmp:
                summary = write_zip(user, tmp)
                size = tmp.tell()
                tmp.seek(0)
                vault_file = VaultFile(owner=user, title=name, key=key, file_type="zip",
                                       bucket=bucket, is_public=False,
                                       notes=_("A copy of your data, made at your request."))
                vault_file.file.save(f"{key}.zip", File(tmp), save=False)
                vault_file.file_size_bytes = size
                vault_file.save()
            try:
                vault_file.content_hash = vault_file.create_hash()
                vault_file.save(update_fields=["content_hash"])
            except Exception:  # noqa: BLE001 - a missing hash is not a failed export
                pass
    except Exception:  # noqa: BLE001 - the member gets a sentence, the log the trace
        log.exception("data export %s failed", export.pk)
        fail(export, _("The export failed. Try again later, or tell an administrator."))
        return export
    export.status = DataExport.READY
    export.output = vault_file
    export.summary = summary
    export.finished_at = timezone.now()
    export.save(update_fields=["status", "output", "summary", "finished_at"])
    audit.export_ready(export)
    return export
