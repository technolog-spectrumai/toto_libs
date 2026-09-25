"""Writing comments: add, edit, withdraw. Every rule is here, once.

A host app attaches a comment by creating its own through-row in the same
transaction as `add` (see models.py for why there is no generic key):

    with transaction.atomic():
        comment = services.add(request.user, form.cleaned_data["body"], reply_to=parent)
        PlaceComment.objects.create(place=place, comment=comment)

and renders a thread with `{% comment_thread comments ... %}`.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .models import Comment

MAX_BODY = 8000


def _clean_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise ValidationError("A comment needs some text.")
    if len(text) > MAX_BODY:
        raise ValidationError(f"A comment is at most {MAX_BODY} characters.")
    return text


def add(author, body: str, *, reply_to: Comment | None = None) -> Comment:
    if not getattr(author, "is_authenticated", False):
        raise PermissionDenied("Sign in to comment.")
    if reply_to is not None and reply_to.is_deleted:
        raise ValidationError("That comment was withdrawn; it cannot be answered.")
    return Comment.objects.create(author=author, body=_clean_body(body), reply_to=reply_to)


def edit(comment: Comment, user, body: str) -> Comment:
    if not comment.may_modify(user):
        raise PermissionDenied("Only the author or staff may change a comment.")
    if comment.is_deleted:
        raise ValidationError("A withdrawn comment cannot be edited.")
    comment.body = _clean_body(body)
    comment.edited_at = timezone.now()
    comment.save(update_fields=["body", "edited_at"])
    return comment


def soft_delete(comment: Comment, user) -> Comment:
    if not comment.may_modify(user):
        raise PermissionDenied("Only the author or staff may withdraw a comment.")
    if not comment.is_deleted:
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
    return comment


def thread(comments) -> list[Comment]:
    """Top-level comments first, each followed by its replies (one level)."""
    rows = list(comments)
    by_parent: dict[int | None, list[Comment]] = {}
    ids = {c.pk for c in rows}
    for c in rows:
        parent = c.reply_to_id if c.reply_to_id in ids else None
        by_parent.setdefault(parent, []).append(c)
    out = []
    for top in by_parent.get(None, []):
        top.depth = 0
        out.append(top)
        for reply in by_parent.get(top.pk, []):
            reply.depth = 1
            out.append(reply)
    return out
