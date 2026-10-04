from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """One thing a member is told (2026-10-04).

    ``kind`` names the sentence (``kinds.py``) and ``params`` fills it in:
    text parameters and ids, never the rendered sentence, so it is said in
    the reader's language whenever the bell is drawn.

    Both account keys CASCADE. ``recipient``: the row is theirs and goes
    when they are erased. ``actor`` — who did it, shown beside the sentence;
    empty for what the platform did itself — goes too, so erasing a member
    takes what they did out of everybody else's bell rather than leaving
    their name there.

    ``collapse_key`` is what a burst folds on (forty uploads to one bucket
    are one row with a count). Read rows are pruned thirty days after
    ``read_at`` (``services.prune``); an unread one waits.
    """

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=40)
    params = models.JSONField(default=dict, blank=True)
    link = models.CharField(max_length=300, blank=True)
    collapse_key = models.CharField(max_length=120, blank=True)
    created = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="notify_recipient_read"),
            models.Index(fields=["recipient", "collapse_key"], name="notify_recipient_key"),
        ]

    def __str__(self):
        return f"{self.kind} → {self.recipient_id}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
