"""What the antivirus remembers.

Two rows and nothing else: what a scan concluded, and what each person wants
scanned automatically.

**A finding never changes the file.** The bytes stay exactly as they were and
stay downloadable. A scanner is a piece of software with opinions, and the cost
of a false positive must not be somebody losing their work. Containment is a
later decision, made deliberately, not a side effect of shipping a scanner.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from toto.vault.scanning import SCANNABLE_TYPES


class ScanVerdict(models.TextChoices):
    CLEAN = "clean", "Clean"
    REFUSED = "refused", "Refused"


class ScanResult(models.Model):
    """One scan of one file's contents, at one moment.

    Keyed on **the hash, not the file**: a verdict is about bytes. Editing a
    file does not make its old verdict wrong, it makes it about something else —
    which is exactly why the clean tick disappears the moment content changes
    rather than lingering on a file nobody has re-checked.
    """

    file = models.ForeignKey(
        "vault.VaultFile", on_delete=models.CASCADE, related_name="scan_results")
    content_sha256 = models.CharField(
        max_length=64, db_index=True,
        help_text="The bytes this verdict is about. Not necessarily current.")
    file_type = models.CharField(max_length=16)

    verdict = models.CharField(max_length=16, choices=ScanVerdict.choices)
    reason = models.CharField(max_length=64, blank=True)
    detail = models.TextField(blank=True)
    line = models.PositiveIntegerField(default=0)

    #: Which entrance this came through — "editor", "upload", "gateway",
    #: "websocket", "restore", "manual". Worth keeping: it is how you find out
    #: which door is letting things in.
    door = models.CharField(max_length=32, blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+")
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scanned_at"]
        verbose_name = "scan result"
        constraints = [
            # One verdict per (file, bytes). Re-scanning unchanged content
            # updates rather than piling up rows.
            models.UniqueConstraint(fields=["file", "content_sha256"],
                                    name="antivirus_one_verdict_per_content"),
        ]
        indexes = [
            # Serves the threats page and the tick lookup respectively.
            models.Index(fields=["verdict", "-scanned_at"]),
            models.Index(fields=["file", "content_sha256"]),
        ]

    def __str__(self):
        return f"{self.file_id} {self.verdict}"

    @property
    def is_threat(self) -> bool:
        return self.verdict == ScanVerdict.REFUSED


class ScanPreference(models.Model):
    """Which file types this person wants screened automatically.

    Per user, and it can only ever narrow **their own** files — nobody can
    weaken screening for anybody else. It governs the automatic doors only: the
    Scan button always works, because pressing it is already a deliberate act.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="scan_preference")
    #: Absent from this list means "do not scan my files of that type on the
    #: way in". Defaults to everything.
    types = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "scan preference"

    def __str__(self):
        return f"{self.user_id}: {', '.join(self.types) or 'nothing'}"

    @classmethod
    def types_for(cls, user) -> tuple[str, ...]:
        """What to scan for this user. Everything, unless they said otherwise."""
        if user is None or not getattr(user, "is_authenticated", False):
            return SCANNABLE_TYPES
        row = cls.objects.filter(user=user).first()
        if row is None:
            return SCANNABLE_TYPES
        return tuple(t for t in row.types if t in SCANNABLE_TYPES)
