"""The privacy notice's pages (2026-10-01, RODO / GDPR).

* ``privacy_notice`` — the current version, public (the host lists it among
  its PUBLIC_ROUTES): an applicant reads it before there is an account. In
  the reader's language, Polish or English; ``?lang=`` shows the other one.
* ``privacy_notice_version`` — any version at its own address, public too:
  what somebody accepted stays readable after it is replaced.
* ``privacy_notice_edit`` — for a superuser on the Superuser plan: the form
  (both texts, pre-filled with the current version) and the list of versions.
  Publishing never edits a version; it adds the next one
  (``toto.socialhub.privacy.publish``, ``PRIVACY.NOTICE_PUBLISHED``). A
  refusal is Post/Redirect/Get, what was typed waiting in the session.

* ``erasure_requests`` — for the same superuser (2026-10-01): the members'
  requests to be erased, open first, each with the console command that
  carries it out. The page never erases; it can only decline, with a note
  (``toto.socialhub.erasure``).

The text is plain text, drawn escaped through ``urlize`` and ``linebreaks``
(the chain the wiki uses) — nothing from the database reaches the page as
HTML.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from toto.socialhub import erasure
from toto.socialhub.models import ErasureRequest, PrivacyNotice
from toto.socialhub.privacy import MAX_TEXT, clean_text, publish
from toto.ui import PageProcessor

#: Versions per page of the editor's list.
PER_PAGE = 10
#: Where a refused publish waits for the editor to draw it.
DRAFT_KEY = "socialhub.privacy_draft"


def may_publish(user) -> bool:
    """A real superuser, and on a host that sells the Superuser plan, that
    plan in force (``toto.subscriptions``). Fails closed: nothing here is
    wrapped, so a fault is an error, never a grant."""
    if user is None or not getattr(user, "is_superuser", False):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.models import superuser_plan_active

    return superuser_plan_active(user)


def _language(request) -> str:
    wanted = (request.GET.get("lang") or "").strip().lower()
    if wanted in ("pl", "en"):
        return wanted
    return "pl" if (translation.get_language() or "").lower().startswith("pl") else "en"


def _show(request, notice):
    language = _language(request)
    current = PrivacyNotice.current()
    context = {
        "notice": notice,
        "text": notice.text_for(language) if notice else "",
        "language": language,
        "other_language": "en" if language == "pl" else "pl",
        "current": current,
        "is_current": notice is not None and current is not None and notice.pk == current.pk,
        "may_publish": may_publish(request.user),
    }
    return render(request, "socialhub/privacy_notice.html", PageProcessor().decorate(context, request))


@require_safe
def privacy_notice(request):
    return _show(request, PrivacyNotice.current())


@require_safe
def privacy_notice_version(request, version):
    notice = PrivacyNotice.objects.filter(version=version).first()
    if notice is None:
        raise Http404(_("There is no such version of the privacy notice."))
    return _show(request, notice)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def privacy_notice_edit(request):
    if not may_publish(request.user):
        raise PermissionDenied(_("The privacy notice is published by a superuser on the Superuser plan."))
    if request.method == "POST":
        return _publish(request)
    current = PrivacyNotice.current()
    draft = request.session.pop(DRAFT_KEY, None) or {}
    page = Paginator(PrivacyNotice.objects.select_related("published_by").order_by("-version"),
                     PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "current": current,
        "text_pl": draft.get("text_pl", current.text_pl if current else ""),
        "text_en": draft.get("text_en", current.text_en if current else ""),
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "max_text": MAX_TEXT,
    }
    return render(request, "socialhub/privacy_notice_edit.html", PageProcessor().decorate(context, request))


def _publish(request):
    text_pl = clean_text(request.POST.get("text_pl"))
    text_en = clean_text(request.POST.get("text_en"))
    current = PrivacyNotice.current()
    error = ""
    if not text_pl or not text_en:
        error = _("Write the notice in both languages.")
    elif len(text_pl) > MAX_TEXT or len(text_en) > MAX_TEXT:
        error = _("Each text may be at most %(max)d characters.") % {"max": MAX_TEXT}
    elif current is not None and (current.text_pl, current.text_en) == (text_pl, text_en):
        error = _("Nothing changed: this is the current version's text.")
    if error:
        # Only a text short enough to be worth keeping goes to the session.
        request.session[DRAFT_KEY] = {"text_pl": text_pl[:MAX_TEXT], "text_en": text_en[:MAX_TEXT]}
        messages.error(request, error)
        return redirect("socialhub:privacy_notice_edit")
    notice = publish(text_pl=text_pl, text_en=text_en, user=request.user)
    messages.success(request, _("Version %(version)d of the privacy notice is published.")
                     % {"version": notice.version})
    return redirect("socialhub:privacy_notice_version", version=notice.version)


#: The list's filters, in the order the tabs show them.
ERASURE_FILTERS = ("open", "done", "declined", "all")


@login_required
@require_safe
def erasure_requests(request):
    if not may_publish(request.user):
        raise PermissionDenied(_("Erasure requests are handled by a superuser on the Superuser plan."))
    shown = request.GET.get("status") or "open"
    if shown not in ERASURE_FILTERS:
        shown = "open"
    tickets = ErasureRequest.objects.select_related("handled_by").order_by("-created_at", "-pk")
    if shown != "all":
        tickets = tickets.filter(status=shown)
    page = Paginator(tickets, PER_PAGE).get_page(request.GET.get("page"))
    for ticket in page:
        ticket.command = erasure.console_command(ticket)
    context = {
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": f"&status={shown}",
        "shown": shown,
        "filters": [(key, label) for key, label in (
            ("open", _("Open")), ("done", _("Carried out")),
            ("declined", _("Declined")), ("all", _("All")))],
        "open_count": ErasureRequest.objects.filter(status=ErasureRequest.OPEN).count(),
    }
    return render(request, "socialhub/erasure_requests.html", PageProcessor().decorate(context, request))


@login_required
@require_POST
def erasure_request_decline(request, pk):
    if not may_publish(request.user):
        raise PermissionDenied(_("Erasure requests are handled by a superuser on the Superuser plan."))
    ticket = ErasureRequest.objects.filter(pk=pk).first()
    if ticket is None:
        raise Http404(_("There is no such erasure request."))
    try:
        erasure.decline(ticket, by=request.user, note=request.POST.get("note", ""), request=request)
    except erasure.Refused as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("The request of %(username)s is declined.")
                         % {"username": ticket.username})
    return redirect("socialhub:erasure_requests")
