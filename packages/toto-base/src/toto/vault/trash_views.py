"""The vault's **Trash** tab (``trash/``, 2026-10-01): the files deleted to
the trash, with Restore and "Delete for good".

Who sees what (``trash.trashed_for``): a member their own trashed files, in
buckets whose clearances let them read — pessimistic, no owner bypass, so a
member who lost a bucket's clearance loses its trash too; a superuser on the
Superuser plan every trashed file. A superuser without the plan is a member
here. Every door looks the file up in that same queryset, so a file one may
not see answers 404 whatever it is.

* **Restore** (``trash.restore_file``) — back to the folder it came from, or
  the bucket's root when that folder is gone; with " (restored)" added when a
  live file there has its name. The message says where it went and what it
  is called.
* **Delete for good** (``trash.purge_trashed``) — the row, its bytes and the
  version bodies only it cited, through ``purge.purge_file``. Confirmed in a
  modal: the form carries ``confirm=yes``, and the server refuses without it.
* **Empty my trash** — Delete for good for every file of one's OWN on the
  page's list (a superuser on the plan empties their own trash too, never
  everybody's), confirmed the same way.

Every act is on the audit chain (``FILE_RESTORED``, ``FILE_PURGED``, one per
file). Post/Redirect/Get back to the page the form was on.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from toto.ui import PageProcessor

from . import trash
from .models import trash_days

#: Trashed files per page.
PER_PAGE = 25


def _back(request):
    """The Trash page again, on the page the form was posted from."""
    url = reverse("vault:trash")
    page = request.POST.get("page", "")
    if page.isascii() and page.isdigit() and page != "1" and len(page) <= 6:
        url += f"?page={page}"
    return redirect(url)


def _confirmed(request) -> bool:
    return request.POST.get("confirm", "") == "yes"


def _row(vault_file, *, now) -> dict:
    folder = vault_file.trashed_from
    return {
        "file": vault_file,
        "bucket": vault_file.bucket,
        "folder": folder.full_path() if folder is not None else "",
        "days_left": trash.days_left(vault_file, now=now),
        "restore_url": reverse("vault:trash_restore", args=[vault_file.pk]),
        "purge_url": reverse("vault:trash_purge", args=[vault_file.pk]),
    }


@login_required
@require_safe
def trash_tab(request):
    rows = trash.trashed_for(request.user).select_related(
        "bucket", "owner", "trashed_by", "trashed_from__parent")
    page_obj = Paginator(rows, PER_PAGE).get_page(request.GET.get("page"))
    now = timezone.now()
    context = {
        "active_tab": "trash",
        "rows": [_row(f, now=now) for f in page_obj.object_list],
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "extra_query": "",
        "sees_all": trash.sees_every_trash(request.user),
        "own_count": trash.trashed_for(request.user).filter(owner=request.user).count(),
        "trash_days": trash_days(),
    }
    return render(request, "vault/trash.html", PageProcessor().decorate(context, request))


@login_required
@require_POST
def trash_restore(request, pk):
    vault_file = get_object_or_404(trash.trashed_for(request.user), pk=pk)
    if vault_file.bucket_id and vault_file.bucket.is_being_deleted:
        from .models import closed_bucket_sentence

        messages.error(request, closed_bucket_sentence(vault_file.bucket))
        return _back(request)
    done = trash.restore_file(vault_file, by=request.user, request=request)
    where = done.directory.full_path() if done.directory is not None else _("the bucket's root")
    said = [_("%(name)s is back in %(where)s.") % {"name": done.title, "where": where}]
    if done.renamed:
        said.append(_("A file there already had its name, so it is called %(name)s now.")
                    % {"name": done.title})
    messages.success(request, " ".join(said))
    return _back(request)


@login_required
@require_POST
def trash_purge(request, pk):
    vault_file = get_object_or_404(trash.trashed_for(request.user), pk=pk)
    if not _confirmed(request):
        messages.error(request, _("Nothing was deleted: confirm Delete for good first."))
        return _back(request)
    title = vault_file.title
    try:
        trash.purge_trashed(vault_file, by=request.user, request=request)
    except ProtectedError:
        messages.error(request, _("%(name)s is still used elsewhere and cannot be deleted yet.")
                       % {"name": title})
        return _back(request)
    messages.success(request, _("%(name)s is deleted for good.") % {"name": title})
    return _back(request)


@login_required
@require_POST
def trash_empty(request):
    if not _confirmed(request):
        messages.error(request, _("Nothing was deleted: confirm Empty my trash first."))
        return _back(request)
    gone = kept = 0
    # One's OWN trash, whoever asks: a superuser on the plan deletes other
    # members' files one by one, never in bulk.
    for vault_file in trash.trashed_for(request.user).filter(owner=request.user):
        try:
            trash.purge_trashed(vault_file, by=request.user, request=request)
            gone += 1
        except ProtectedError:
            kept += 1
    if kept:
        messages.warning(request, _("%(gone)d deleted for good; %(kept)d still used elsewhere "
                                    "and kept.") % {"gone": gone, "kept": kept})
    else:
        messages.success(request, _("Your trash is empty: %(gone)d deleted for good.")
                         % {"gone": gone})
    return redirect("vault:trash")
