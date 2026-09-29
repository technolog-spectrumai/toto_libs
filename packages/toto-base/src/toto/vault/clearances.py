"""Who reads a file: its clearances (2026-09-29).

The rule is ``socialhub.clearance_access`` (no clearance → the vault's five
clauses; clearances → their members, the owner, superusers) and it is enforced
in ``access.may_read`` and ``filetree.accessible_files``. This module is the
door for CHANGING a file's clearances — one page, ``files/<pk>/access/``, that
the apps showing files (sheets, decks) link to from their own toolbars with
``?next=`` — and the helpers their templates ask.

Who may change them: the file's owner, and superusers.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from toto.socialhub import clearance_access
from toto.ui import PageProcessor

from .models import VaultFile

ROWS = "clearance_rows"


def may_manage(user, vault_file) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.is_superuser or vault_file.owner_id == user.pk


def clearances_of(vault_file) -> list:
    return clearance_access.clearances_of(vault_file, rows=ROWS)


def set_clearances(vault_file, clearances, *, actor):
    return clearance_access.set_clearances(vault_file, clearances, rows=ROWS, actor=actor,
                                     action="file.clearances_changed", app_label="vault",
                                     file=vault_file.pk, title=vault_file.title,
                                     file_type=vault_file.file_type)


def access_url(vault_file, next_url: str = "") -> str:
    from django.urls import reverse
    from django.utils.http import urlencode

    url = reverse("vault:file_access", args=[vault_file.pk])
    return f"{url}?{urlencode({'next': next_url})}" if next_url else url


def _ids(values) -> set:
    return {int(v) for v in values if str(v).isascii() and str(v).isdigit() and len(str(v)) <= 18}


@login_required
@require_http_methods(["GET", "POST"])
def file_access(request, pk):
    """The file's clearances, chosen by its owner or a superuser. A member who
    may not even read the file gets the 404 a missing file gets."""
    from .access import may_read

    vault_file = get_object_or_404(VaultFile.objects.select_related("owner", "bucket"), pk=pk)
    if not may_read(request.user, vault_file):
        raise Http404("No such file.")
    if not may_manage(request.user, vault_file):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden(_("Only the file's owner or a superuser decides who reads it."))
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ""
    choices = clearance_access.shareable_clearances(request.user, vault_file, rows=ROWS)
    if request.method == "POST":
        picked = choices.filter(pk__in=_ids(request.POST.getlist("clearance")))
        before, after = set_clearances(vault_file, picked, actor=request.user)
        messages.success(request, _("Saved.") if before != after else _("Nothing changed."))
        return redirect(next_url or access_url(vault_file))
    current = {c.pk for c in clearances_of(vault_file)}
    return render(request, "vault/file_access.html", PageProcessor().decorate({
        "vault_file": vault_file,
        "clearances": [{"clearance": c, "on": c.pk in current} for c in choices],
        "restricted": bool(current),
        "next": next_url,
        "active_tab": "files",
    }, request))
