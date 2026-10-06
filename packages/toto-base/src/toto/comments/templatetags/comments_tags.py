import uuid

from django import template

from .. import services

register = template.Library()


@register.inclusion_tag("comments/_thread.html", takes_context=True)
def comment_thread(context, comments, post_url, edit_url_name, delete_url_name,
                   parent_pk=None, verdicts=None, price_metric=None, op=False):
    """A thread: the comments, their replies, the reply and edit forms.

    `edit_url_name` / `delete_url_name` are the host app's route names; each
    is reversed with `(parent_pk, comment.pk)`, so the host decides the URL
    shape and keeps its own permission check on the way in.

    Three optional things a caller with rules of its own may hand over
    (2026-10-06, geography's pins and zones); without them the thread is what
    it always was:

    `verdicts`: `{comment.pk: (can_edit, can_withdraw)}`, the caller's own
    answer for each row. A row it does not name gets neither button. Without
    it both come from `Comment.may_modify` (the author, or staff).

    `price_metric`: a metric code; its price is shown beside Comment and Reply.

    `op`: a hidden `op` field in the forms that add a comment, a fresh UUID
    per form and per rendering, for a door that charges and must tell a
    second press of one form from a new comment.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    rows = services.thread(comments)
    for row in rows:
        if verdicts is None:
            row.can_edit = row.can_withdraw = row.may_modify(user)
        else:
            row.can_edit, row.can_withdraw = verdicts.get(row.pk, (False, False))
        row.can_modify = row.can_edit or row.can_withdraw
        row.op = str(uuid.uuid4()) if op else ""
    return {"comments": rows, "post_url": post_url, "edit_url_name": edit_url_name,
            "delete_url_name": delete_url_name, "parent_pk": parent_pk,
            "user": user, "csrf_token": context.get("csrf_token"),
            "darkMode": context.get("darkMode"), "request": request,
            "price_metric": price_metric or "",
            "op": str(uuid.uuid4()) if op else ""}
