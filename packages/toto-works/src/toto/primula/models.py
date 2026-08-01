from django.conf import settings
from django.db import models


class SheetVersion(models.Model):
    """A saved snapshot of a Primula sheet — the primitive versioning.

    One row is appended on every save; the newest :data:`VERSION_CAP` per sheet are
    kept and older ones pruned (see ``toto.primula.views.snapshot_version``). The
    *current* sheet content always lives in the vault file; these rows are its history,
    each restorable. Rows die with the sheet (``on_delete=CASCADE``).
    """

    sheet_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.CASCADE,
        related_name="sheet_versions",
    )
    snapshot = models.TextField(help_text="The Univer workbook snapshot JSON at save time.")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primula_versions",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["sheet_file", "created_at"])]

    def __str__(self):
        return f"sheet {self.sheet_file_id} @ {self.created_at:%Y-%m-%d %H:%M}"
