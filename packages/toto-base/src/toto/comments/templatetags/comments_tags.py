from django import template

from .. import services

register = template.Library()


@register.inclusion_tag("comments/_thread.html", takes_context=True)
def comment_thread(context, comments, post_url, edit_url_name, delete_url_name,
                   parent_pk=None):
    """A thread: the comments, their replies, the reply and edit forms.

    `edit_url_name` / `delete_url_name` are the host app's route names; each
    is reversed with `(parent_pk, comment.pk)`, so the host decides the URL
    shape and keeps its own permission check on the way in.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    rows = services.thread(comments)
    for row in rows:
        row.can_modify = row.may_modify(user)
    return {"comments": rows, "post_url": post_url, "edit_url_name": edit_url_name,
            "delete_url_name": delete_url_name, "parent_pk": parent_pk,
            "user": user, "csrf_token": context.get("csrf_token"),
            "darkMode": context.get("darkMode")}
