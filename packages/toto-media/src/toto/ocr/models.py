"""What OCR keeps: a run, its pages, its settings, and its meter.

This app had no tables until 1.51 and its docstring said so. That changed when
reading became a job: a scanned book is hundreds of pages, each one its own
Celery task, and progress that survives a page failing cannot live in a request.

The shape is `toto.vault.transfer.TransferRun`'s, and its three doctrines carry
over intact:

* **The denominator is frozen at creation, never counted live.** A progress bar
  whose total moves is a progress bar nobody can read.
* **The cursor — here, the page row — is written in the same transaction as the
  work it accounts for**, so a run killed anywhere resumes exactly where the
  rows say it stopped and cannot double-charge.
* **Partial completion is not failure.** FAILED means the run itself could not
  proceed; a run that read 297 of 300 pages did what could be done, and the
  three that did not say why.

What TransferRun does not have and this does is a child row per item, because
the requirement is that pages run, retry and fail independently.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.vault.storage import private_storage

#: How many per-page failures a run stores verbatim. Past this the page rows
#: still carry every error; this is the summary the status poll reads, and it
#: is capped so a 400-page disaster stays one cheap row read. TransferRun caps
#: its `skips` list at 50 for the same reason.
MAX_PAGE_ERRORS = 50


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to start")
    RUNNING = "running", _("Reading")
    SUCCESS = "success", _("Done")
    #: Some pages read, some did not. This exists because two rules collide
    #: without it: "partial completion is SUCCESS" and "the source is deleted
    #: as soon as the last page is read". Apply both to a run with one bad page
    #: in 300 and the source is gone, so that page can never be retried — which
    #: contradicts the whole point of a page being its own task.
    PARTIAL = "partial", _("Done, with pages missing")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


TERMINAL = {RunStatus.SUCCESS, RunStatus.PARTIAL,
            RunStatus.FAILED, RunStatus.CANCELLED}


class PageStatus(models.TextChoices):
    WAITING = "waiting", _("Waiting")
    RUNNING = "running", _("Reading")
    DONE = "done", _("Read")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class OcrSettings(models.Model):
    """Singleton configuration, editable in the admin.

    A Django setting would have been one line and wrong for this: how large an
    upload this deployment accepts is an operator's decision about THIS server,
    and an operator should be able to change it without a redeploy. Same shape
    as toto.ambrosia's AmbrosiaSettings and toto.weather's providers.

    What is configurable is bounded on purpose. The ceiling on `max_upload_mb`
    is not timidity and not a preference: nginx cannot read this table, so the
    location deploy.py writes for /ocr/ has to permit anything this field can
    legally hold. 256 is that number, in both places, and each says so.
    """

    max_upload_mb = models.PositiveIntegerField(
        default=64,
        validators=[MinValueValidator(1), MaxValueValidator(256)],
        verbose_name=_("Maximum upload size (MB)"),
        help_text=_(
            "How large a file may be submitted for reading. The default of 64 "
            "MB fits an ordinary scanned book — 300 grayscale pages in a PDF "
            "is about 19 MB — with room to spare. The ceiling of 256 is fixed "
            "in the web server's own configuration and cannot be raised here."
        ),
    )
    max_pages_per_run = models.PositiveIntegerField(
        default=400,
        validators=[MinValueValidator(1), MaxValueValidator(2000)],
        verbose_name=_("Maximum pages in one job"),
        help_text=_(
            "A page takes a few seconds of processor time, and this server "
            "runs every background job on one queue. 400 pages is a long book "
            "and roughly half an hour of work; beyond that a single job starts "
            "delaying everything else."
        ),
    )
    retention_days = models.PositiveIntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        verbose_name=_("Keep finished jobs for (days)"),
        help_text=_(
            "Recognised text is removed after this long. The uploaded file "
            "itself is deleted as soon as the last page is read; a job that "
            "failed keeps its file for this period so it can be retried."
        ),
    )
    retention_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Remove finished jobs automatically"),
        help_text=_(
            "Turn off only if something else is responsible for clearing them. "
            "With this off, uploaded scans and their text are kept forever."
        ),
    )

    class Meta:
        verbose_name = _("Text recognition settings")
        verbose_name_plural = _("Text recognition settings")

    def __str__(self) -> str:
        return "Text recognition settings"

    @classmethod
    def get(cls) -> "OcrSettings":
        """The one row, created on first read so a fresh install has defaults."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def _default(cls, field: str):
        return cls._meta.get_field(field).default

    @classmethod
    def max_upload_bytes(cls) -> int:
        """The cap, in bytes. Never raises: a settings row that cannot be read
        must not be the reason nobody can read a scan, so a database mid-migration
        falls back to the field's own default."""
        try:
            return int(cls.get().max_upload_mb) * 1024 * 1024
        except Exception:  # noqa: BLE001 — the field default is the safe answer
            return int(cls._default("max_upload_mb")) * 1024 * 1024

    @classmethod
    def page_cap(cls) -> int:
        try:
            return int(cls.get().max_pages_per_run)
        except Exception:  # noqa: BLE001
            return int(cls._default("max_pages_per_run"))

    @classmethod
    def boundary(cls):
        """The cutoff finished runs are removed before, or None when off.

        THE one derivation. The sweep, the admin help text and any future
        preview screen all call this; nothing else computes a cutoff, which is
        the rule toto.forum's retention policy states and the reason its
        boundary is a method rather than an expression repeated three times.
        """
        from django.utils import timezone
        try:
            row = cls.get()
            if not row.retention_enabled:
                return None
            days = int(row.retention_days)
        except Exception:  # noqa: BLE001
            days = int(cls._default("retention_days"))
        return timezone.now() - timezone.timedelta(days=days)


class OcrRun(models.Model):
    """One submission: a file, the pages it was cut into, and the text."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, related_name="ocr_runs")
    status = models.CharField(max_length=16, choices=RunStatus.choices,
                              default=RunStatus.PENDING)
    error = models.TextField(blank=True)

    #: The uploaded bytes, on the vault's private storage so no URL can ever be
    #: minted for them. Blank on a run started from a file already in the vault.
    source = models.FileField(upload_to="ocr/sources/", blank=True,
                              storage=private_storage)
    #: SET_NULL, not CASCADE: a deleted vault file must not erase the record
    #: that work was done and billed for it.
    source_file = models.ForeignKey("vault.VaultFile", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="ocr_runs")
    #: A GROUP of images submitted as one job: `[{"name": ..., "path": ...}]`,
    #: in page order, where `path` is a storage name on the same private
    #: storage as `source`. Frozen at creation for the reason TransferRun
    #: freezes `file_ids` — a selection is what the user saw when they pressed
    #: the button, and re-deriving it later would answer a different question.
    #: Empty for a PDF or a single image, which use `source`.
    sources = models.JSONField(default=list, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    source_bytes = models.PositiveBigIntegerField(default=0)
    #: "image" or "pdf", as VaultFile.detect_type spells them.
    source_type = models.CharField(max_length=16, blank=True)
    language = models.CharField(max_length=32, default="eng")

    #: THE FROZEN DENOMINATOR. Null only for a run that failed before the page
    #: count was known, which renders an indeterminate bar rather than a lie.
    total_pages = models.PositiveIntegerField(null=True, blank=True)
    pages_done = models.PositiveIntegerField(default=0)
    pages_failed = models.PositiveIntegerField(default=0)
    #: done + failed + cancelled. The equality test on this is what makes
    #: "am I the last page?" a single atomic decision.
    pages_settled = models.PositiveIntegerField(default=0)

    page_errors = models.JSONField(default=list, blank=True)

    #: The combined result, written once by the finaliser, in page order.
    text = models.TextField(blank=True)

    #: Whether the antivirus actually looked. A PDF over the scanner's own size
    #: cap is passed through unscanned rather than refused, and the page says so
    #: — "we checked" and "we did not check" must never look the same.
    scanned = models.BooleanField(default=False)
    scan_note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="ocr_run_owner_idx"),
            models.Index(fields=["status"], name="ocr_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"OCR #{self.pk} — {self.source_name or 'untitled'}"

    @property
    def is_finished(self) -> bool:
        return self.status in TERMINAL

    @property
    def percent(self):
        """Progress, or None when there is no denominator.

        None renders an indeterminate bar. A run whose total is unknown has no
        percentage, and inventing one would be a number that means nothing.
        """
        if not self.total_pages:
            return None
        return round(100 * self.pages_settled / self.total_pages)

    def add_page_error(self, number: int, reason: str, attempts: int = 1) -> None:
        errors = list(self.page_errors or [])
        if len(errors) < MAX_PAGE_ERRORS:
            errors.append({"page": number, "reason": reason[:300],
                           "attempts": attempts})
        self.page_errors = errors

    def retry_page_numbers(self) -> list:
        """The pages a retry would attempt: the ones that did not deliver."""
        return list(
            self.pages.filter(status__in=[PageStatus.FAILED, PageStatus.CANCELLED])
            .order_by("number").values_list("number", flat=True)
        )

    def source_for(self, number: int) -> str:
        """The storage name backing page `number`.

        A group of images is one file per page; a PDF is one file for all of
        them. Both answer here so the page task never has to know which it got.
        """
        if self.sources:
            return (self.sources[number - 1] or {}).get("path", "")
        return self.source.name or ""

    def discard_source(self) -> None:
        """Delete the uploaded bytes. THE only place they are unlinked.

        Deleting the row does not delete the blob — a FileField leaves it on
        disk — so this is explicit and called from exactly three places: the
        finaliser on success, the cancel closer when nothing is in flight, and
        the retention sweep. Deliberately not a post_delete signal: a signal
        that destroys bytes is invisible at the call site.
        """
        from django.core.files.storage import default_storage

        if self.source:
            self.source.delete(save=False)
            self.source = ""
        for entry in list(self.sources or []):
            name = (entry or {}).get("path")
            if not name:
                continue
            try:
                self.source.storage.delete(name)
            except Exception:  # noqa: BLE001 — a blob already gone is fine
                pass
        self.sources = []
        self.save(update_fields=["source", "sources"])


class OcrPage(models.Model):
    """One page of one run, and its own Celery task."""

    run = models.ForeignKey(OcrRun, on_delete=models.CASCADE, related_name="pages")
    #: 1-based, and the ONLY thing that decides where this text lands in the
    #: result. Completion order is not order.
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=PageStatus.choices,
                              default=PageStatus.WAITING)
    text = models.TextField(blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            # The idempotency backstop: Celery can deliver a task twice, and a
            # second row for the same page would double-count and double-charge.
            models.UniqueConstraint(fields=["run", "number"],
                                    name="ocr_one_row_per_page"),
        ]

    def __str__(self) -> str:
        return f"page {self.number} of OCR #{self.run_id}"


class OcrUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Text recognition usage event"
        verbose_name_plural = "Text recognition usage events"


class OcrQuotaPolicy(AbstractQuotaPolicy):
    events = OcrUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Text recognition quota policy"
        verbose_name_plural = "Text recognition quota policies"
