"""One comment, and nothing about what it is on.

A comment knows who wrote it, what it says, what it answers and when it was
changed or withdrawn. It does NOT know what it is attached to: each app that
wants comments declares its own through-table with a real foreign key —
`places.PlaceComment(place FK, comment OneToOne)` — the way the vault's
attachments work (`toto/vault/attach.py` says why there is no
GenericForeignKey on this platform: a generic key cannot be joined, cannot be
constrained and cannot cascade). So deleting a place deletes its through-rows
and, by CASCADE on the OneToOne, its comments; a comment is never orphaned
into a table that cannot say what it belonged to.

`author` is the user (who acted), as `forum.ForumMessage.sender` is; the
Person is reachable from it where a page wants the display name.
"""

from django.conf import settings
from django.db import models


class Comment(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    body = models.TextField()
    reply_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="replies")
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    #: Withdrawn, not erased: a thread keeps its shape and the audit keeps its
    #: text; the page shows "withdrawn" in its place.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=["reply_to", "created_at"])]

    def __str__(self):
        return f"comment {self.pk} by {self.author_id}"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def may_modify(self, user) -> bool:
        """The author, or staff. Nobody else edits or withdraws a comment."""
        if not getattr(user, "is_authenticated", False):
            return False
        return user.is_staff or user.is_superuser or self.author_id == user.pk
